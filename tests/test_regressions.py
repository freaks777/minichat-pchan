import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEST_TMP = ROOT / ".test-tmp"
TEST_TMP.mkdir(exist_ok=True)

from core import api as core_api
from core.api import _anthropic_messages, _gemini_contents
from core.config import (
    validate_api_settings,
    validate_session_settings,
    validate_style_settings,
    validate_watchdog_settings,
)
from core.history import History
from core.persona_manager import PersonaManager
from plugins.base import PluginBase
from plugins.plugin_manager import PluginManager
from plugins.memory.plugin import (
    MemoryPlugin,
    deduplicate_facts,
    fact_id,
    normalize_fact,
)
from plugins.session_log.plugin import SessionLogPlugin
from plugins.secrets.plugin import SecretsPlugin
from plugins.persona_studio.plugin import PersonaStudioPlugin
from plugins.watchdog.plugin import WatchdogPlugin


class ProviderConversionTests(unittest.TestCase):
    def test_all_system_messages_are_preserved(self):
        messages = [
            {"role": "system", "content": "SOUL"},
            {"role": "system", "content": "SKILL"},
            {"role": "system", "content": "CONSTRAINT"},
            {"role": "user", "content": "hello"},
        ]

        anthropic_system, anthropic_messages = _anthropic_messages(messages)
        gemini_system, gemini_messages = _gemini_contents(messages)

        self.assertEqual(anthropic_system, "SOUL\n\nSKILL\n\nCONSTRAINT")
        self.assertEqual(gemini_system, "SOUL\n\nSKILL\n\nCONSTRAINT")
        self.assertEqual(anthropic_messages[0]["content"], "hello")
        self.assertEqual(gemini_messages[0]["parts"][0]["text"], "hello")


class DependencyCompatibilityTests(unittest.TestCase):
    def test_memory_dependencies_are_declared_and_importable(self):
        import importlib
        from importlib.metadata import version
        from packaging.specifiers import SpecifierSet

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for requirement in (
            "sentence-transformers==5.6.0",
            "transformers==5.12.1",
            "huggingface-hub>=1.5.0,<2.0",
            "chromadb==1.5.9",
        ):
            self.assertIn(requirement, requirements.splitlines())

        self.assertEqual(version("sentence-transformers"), "5.6.0")
        self.assertEqual(version("transformers"), "5.12.1")
        self.assertIn(version("huggingface-hub"), SpecifierSet(">=1.5.0,<2.0"))
        self.assertEqual(version("chromadb"), "1.5.9")
        for module in (
            "sentence_transformers",
            "transformers",
            "huggingface_hub",
            "chromadb",
        ):
            with self.subTest(module=module):
                importlib.import_module(module)

    def test_application_log_messages_are_cp932_encodable(self):
        sources = {
            "main": (ROOT / "backend" / "main.py").read_text(encoding="utf-8"),
            "mail": (ROOT / "backend" / "plugins" / "mail" / "plugin.py").read_text(
                encoding="utf-8"
            ),
            "watchdog": (
                ROOT / "backend" / "plugins" / "watchdog" / "plugin.py"
            ).read_text(encoding="utf-8"),
            "studio": (
                ROOT / "backend" / "plugins" / "persona_studio" / "plugin.py"
            ).read_text(encoding="utf-8"),
        }
        messages = {
            "main": [
                "frontend/ directory not found at %s - static files unavailable",
                ".env file not found - API keys may be missing",
            ],
            "mail": [
                "mail: env vars not set: %s - email notifications will not work",
            ],
            "watchdog": [
                "watchdog: disabled by config - not starting monitor",
            ],
            "studio": [
                "%s fallback error: %s - %s",
                "%s JSON parse failed: %s - %s",
                "%s error: %s - %s",
            ],
        }

        for source_name, expected in messages.items():
            for message in expected:
                with self.subTest(source=source_name, message=message):
                    self.assertIn(message, sources[source_name])
                    message.encode("cp932")

class HttpClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await core_api.close_http_client()

    async def test_init_reuses_client_and_close_releases_it(self):
        client = mock.MagicMock()
        client.is_closed = False
        client.aclose = mock.AsyncMock()
        limits = mock.sentinel.limits

        with (
            mock.patch("core.api.httpx.Limits", return_value=limits) as limits_mock,
            mock.patch("core.api.httpx.AsyncClient", return_value=client) as client_mock,
        ):
            first = core_api.init_http_client()
            second = core_api.init_http_client()

        self.assertIs(first, client)
        self.assertIs(second, client)
        limits_mock.assert_called_once_with(
            max_connections=20,
            max_keepalive_connections=10,
        )
        client_mock.assert_called_once_with(limits=limits)

        await core_api.close_http_client()

        client.aclose.assert_awaited_once()
        self.assertIsNone(core_api._http_client)

    async def test_sync_requests_reuse_client_and_keep_request_timeout(self):
        response = mock.MagicMock()
        response.json.return_value = {"content": [{"text": "ok"}]}
        client = mock.MagicMock()
        client.is_closed = False
        client.aclose = mock.AsyncMock()
        client.post = mock.AsyncMock(return_value=response)
        core_api._http_client = client
        provider = {
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "test",
        }
        config = {"api": {"timeout": 37, "max_tokens": 100}}

        first = await core_api._anthropic_sync([], provider, "model", config)
        second = await core_api._anthropic_sync([], provider, "model", config)

        self.assertEqual((first, second), ("ok", "ok"))
        self.assertEqual(client.post.await_count, 2)
        for call in client.post.await_args_list:
            self.assertEqual(call.kwargs["timeout"].connect, 37)

    def test_all_six_api_paths_use_shared_client_and_lifespan_closes_it(self):
        api_source = (ROOT / "backend" / "core" / "api.py").read_text(
            encoding="utf-8"
        )
        main_source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

        self.assertEqual(
            api_source.count("async with _http_client_context() as client:"),
            6,
        )
        self.assertNotIn(
            "async with httpx.AsyncClient(timeout=timeout) as client:",
            api_source,
        )
        self.assertLess(
            main_source.index("init_http_client()"),
            main_source.index("        yield"),
        )
        self.assertLess(
            main_source.index("await plugin_manager.shutdown_all()"),
            main_source.index("await close_http_client()"),
        )


class PluginUiTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def plugin(
        name="demo",
        priority=50,
        definition=None,
        result=None,
        error=None,
    ):
        class StubPlugin(PluginBase):
            hooks = []

            def __init__(self):
                self.name = name
                self.priority = priority

            async def run(self, hook, data, ctx):
                return None

            def get_ui_slot(self):
                if error == "definition":
                    raise RuntimeError("definition failed")
                return definition

            async def handle_ui_action(self, action, payload, ctx):
                if error == "action":
                    raise RuntimeError("action failed")
                return result or {"status": "ok", "message": "done", "data": payload}

        return StubPlugin()

    @staticmethod
    def definition(slot="chat.toolbar", components=None):
        return {
            "slot": slot,
            "components": components or [{
                "type": "button",
                "id": "demo-button",
                "label": "Demo",
                "action": "run",
                "disabled": False,
            }],
        }

    @staticmethod
    def form_component(form_id="search-form", action="search", fields=None, disabled=False):
        return {
            "type": "form",
            "id": form_id,
            "action": action,
            "submit_label": "Search",
            "disabled": disabled,
            "fields": fields or [{
                "id": "query",
                "label": "Query",
                "required": True,
                "max_length": 200,
                "placeholder": "Enter query",
                "value": "",
            }],
        }

    def manager(self, plugins):
        manager = PluginManager.__new__(PluginManager)
        manager.plugins = sorted(plugins, key=lambda plugin: plugin.priority)
        manager._secret_validator = None
        return manager

    @staticmethod
    def secret_field(required=False):
        return {
            "type": "secret", "id": "token", "label": "API token",
            "required": required, "placeholder": "Stored locally",
        }

    def test_collects_valid_definitions_in_priority_order_and_isolates_failures(self):
        late = self.plugin("late", 80, self.definition("chat.input_actions"))
        early = self.plugin("early", 10, self.definition())
        no_ui = self.plugin("none", 20, None)
        broken = self.plugin("broken", 30, self.definition(), error="definition")
        invalid = self.plugin("invalid", 40, {"slot": "studio.actions", "components": []})
        manager = self.manager([late, invalid, broken, no_ui, early])

        with self.assertLogs("rp-standalone", level="WARNING"):
            definitions = manager.collect_ui_definitions()

        self.assertEqual([item["name"] for item in definitions], ["early", "late"])
        self.assertEqual(
            [item["slot"] for item in definitions],
            ["chat.toolbar", "chat.input_actions"],
        )

    def test_accepts_legacy_and_multi_slot_definitions_in_declared_order(self):
        legacy = self.plugin("legacy", 10, self.definition("chat.toolbar"))
        multi = self.plugin("multi", 20, [
            self.definition("settings.plugins", components=[{
                "type": "button", "id": "settings-run", "label": "Run",
                "action": "run",
            }]),
            self.definition("studio.actions", components=[{
                "type": "status", "id": "studio-state", "text": "Ready",
                "level": "info",
            }]),
        ])
        manager = self.manager([multi, legacy])

        definitions = manager.collect_ui_definitions()

        self.assertEqual(
            [(item["name"], item["slot"]) for item in definitions],
            [
                ("legacy", "chat.toolbar"),
                ("multi", "settings.plugins"),
                ("multi", "studio.actions"),
            ],
        )

    def test_accepts_four_definitions_and_rejects_invalid_multi_slot_sets(self):
        slots = [
            "chat.input_actions", "chat.toolbar", "studio.actions", "settings.plugins",
        ]
        valid = [
            self.definition(slot, components=[{
                "type": "status", "id": f"state-{index}", "text": "Ready",
                "level": "info",
            }])
            for index, slot in enumerate(slots)
        ]
        plugin = self.plugin("demo", definition=valid)
        self.assertEqual(
            len(PluginManager._validate_ui_definitions(plugin, valid)), 4
        )

        duplicate_slot = [valid[0], {
            **valid[1], "slot": valid[0]["slot"],
        }]
        duplicate_id = [valid[0], {
            **valid[1], "components": [{
                "type": "status", "id": "state-0", "text": "Other", "level": "info",
            }],
        }]
        invalid_definition = [valid[0], {"slot": "studio.actions", "components": []}]
        cases = [
            [],
            valid + [self.definition("chat.toolbar")],
            [valid[0], "not a definition"],
            duplicate_slot,
            duplicate_id,
            invalid_definition,
        ]
        for definitions in cases:
            with self.subTest(definitions=definitions):
                self.assertIsNone(
                    PluginManager._validate_ui_definitions(plugin, definitions)
                )

    def test_invalid_multi_slot_plugin_does_not_hide_other_plugins(self):
        duplicate = [
            self.definition("chat.toolbar", components=[{
                "type": "status", "id": "same", "text": "One", "level": "info",
            }]),
            self.definition("settings.plugins", components=[{
                "type": "status", "id": "same", "text": "Two", "level": "info",
            }]),
        ]
        invalid = self.plugin("invalid", 10, duplicate)
        valid = self.plugin("valid", 20, self.definition())
        manager = self.manager([valid, invalid])

        with self.assertLogs("rp-standalone", level="WARNING"):
            definitions = manager.collect_ui_definitions()

        self.assertEqual([item["name"] for item in definitions], ["valid"])

    async def test_dispatch_aggregates_actions_and_statuses_across_slots(self):
        definitions = [
            self.definition("chat.toolbar", components=[
                {"type": "button", "id": "disabled-run", "label": "Run",
                 "action": "shared", "disabled": True},
            ]),
            self.definition("settings.plugins", components=[
                {"type": "status", "id": "remote-state", "text": "Ready", "level": "info"},
                {"type": "button", "id": "enabled-run", "label": "Run",
                 "action": "shared", "disabled": False},
            ]),
        ]
        result = {"status": "ok", "message": "done", "data": {"ui_updates": [{
            "component_id": "remote-state", "text": "Connected", "level": "success",
        }]}}
        manager = self.manager([
            self.plugin("demo", definition=definitions, result=result)
        ])

        actual = await manager.dispatch_ui_action("demo", "shared", {})

        self.assertEqual(actual["status"], "ok")
        self.assertEqual(actual["data"]["ui_updates"][0]["component_id"], "remote-state")

        all_disabled = [definitions[0], {
            **definitions[1],
            "components": [
                definitions[1]["components"][0],
                {"type": "button", "id": "other-disabled", "label": "Run",
                 "action": "shared", "disabled": True},
            ],
        }]
        disabled_manager = self.manager([
            self.plugin("disabled", definition=all_disabled)
        ])
        with self.assertRaises(KeyError):
            await disabled_manager.dispatch_ui_action("disabled", "shared", {})

    def test_accepts_and_normalizes_text_form_definition(self):
        component = self.form_component()
        definition = self.definition(components=[component])
        plugin = self.plugin("demo", definition=definition)

        normalized = PluginManager._validate_ui_definition(plugin, definition)

        self.assertEqual(normalized["components"][0]["fields"][0]["type"], "text")
        boundary_fields = [
            {
                "id": f"field-{index}", "label": "Field", "required": False,
                "max_length": 2000, "placeholder": "x" * 100, "value": "x" * 2000,
            }
            for index in range(10)
        ]
        boundary = self.definition(components=[
            self.form_component(fields=boundary_fields)
        ])
        self.assertIsNotNone(PluginManager._validate_ui_definition(plugin, boundary))

    def test_accepts_and_normalizes_textarea_and_select_fields(self):
        fields = [
            {
                "type": "textarea", "id": "notes", "label": "Notes",
                "required": False, "max_length": 2000,
                "placeholder": "Details", "value": "Initial",
            },
            {
                "type": "select", "id": "mode", "label": "Mode",
                "required": True, "options": [
                    {"value": "safe", "label": "Safe"},
                    {"value": "fast", "label": "Fast"},
                ],
            },
        ]
        definition = self.definition(components=[self.form_component(fields=fields)])
        plugin = self.plugin("demo", definition=definition)

        normalized = PluginManager._validate_ui_definition(plugin, definition)

        actual_fields = normalized["components"][0]["fields"]
        self.assertEqual(actual_fields[0]["type"], "textarea")
        self.assertEqual(actual_fields[1]["type"], "select")
        self.assertEqual(actual_fields[1]["value"], "safe")
        self.assertEqual(len(actual_fields[1]["options"]), 2)

    def test_rejects_invalid_textarea_and_select_definitions(self):
        plugin = self.plugin("demo", definition=self.definition())
        textarea = {
            "type": "textarea", "id": "notes", "label": "Notes",
            "required": False, "max_length": 20, "placeholder": "", "value": "",
        }
        select = {
            "type": "select", "id": "mode", "label": "Mode", "required": True,
            "options": [
                {"value": "safe", "label": "Safe"},
                {"value": "fast", "label": "Fast"},
            ],
            "value": "safe",
        }
        invalid_fields = [
            {**textarea, "type": "password"},
            {**textarea, "options": []},
            {**select, "options": []},
            {**select, "options": select["options"] * 26},
            {**select, "options": [select["options"][0]] * 2},
            {**select, "options": [{"value": "x", "label": ""}]},
            {**select, "options": [{"value": "x", "label": "X", "extra": True}]},
            {**select, "value": "missing"},
            {**select, "max_length": 10},
        ]
        for field in invalid_fields:
            with self.subTest(field=field):
                definition = self.definition(components=[
                    self.form_component(fields=[field])
                ])
                self.assertIsNone(
                    PluginManager._validate_ui_definition(plugin, definition)
                )

    def test_accepts_and_rejects_checkbox_definitions(self):
        plugin = self.plugin("demo", definition=self.definition())
        checkbox = {
            "type": "checkbox", "id": "confirm", "label": "Confirm",
            "required": True, "value": False,
        }
        definition = self.definition(components=[
            self.form_component(fields=[checkbox])
        ])

        normalized = PluginManager._validate_ui_definition(plugin, definition)

        self.assertEqual(normalized["components"][0]["fields"][0], checkbox)
        invalid_fields = [
            {**checkbox, "value": 0},
            {**checkbox, "value": 1},
            {**checkbox, "value": "true"},
            {**checkbox, "value": None},
            {**checkbox, "extra": True},
            {key: value for key, value in checkbox.items() if key != "value"},
        ]
        for field in invalid_fields:
            with self.subTest(field=field):
                invalid = self.definition(components=[
                    self.form_component(fields=[field])
                ])
                self.assertIsNone(
                    PluginManager._validate_ui_definition(plugin, invalid)
                )

    def test_accepts_and_rejects_number_definitions(self):
        plugin = self.plugin("demo", definition=self.definition())
        number = {
            "type": "number", "id": "amount", "label": "Amount",
            "required": False, "min": -1e15, "max": 1e15, "value": None,
        }
        definition = self.definition(components=[self.form_component(fields=[number])])
        normalized = PluginManager._validate_ui_definition(plugin, definition)
        self.assertEqual(normalized["components"][0]["fields"][0], number)
        invalid = [
            {**number, "value": True}, {**number, "value": "1"},
            {**number, "value": float("nan")}, {**number, "value": float("inf")},
            {**number, "value": 1e16}, {**number, "min": 2, "max": 1},
            {**number, "min": 0, "value": -1}, {**number, "extra": 1},
        ]
        for field in invalid:
            with self.subTest(field=field):
                item = self.definition(components=[self.form_component(fields=[field])])
                self.assertIsNone(PluginManager._validate_ui_definition(plugin, item))
    def test_rejects_invalid_text_form_definitions(self):
        plugin = self.plugin("demo", definition=self.definition())
        valid = self.form_component()
        field = valid["fields"][0]
        invalid_components = [
            {**valid, "html": "<b>x</b>"},
            {**valid, "id": "bad id"},
            {**valid, "action": "bad action"},
            {**valid, "submit_label": ""},
            {**valid, "disabled": "false"},
            {**valid, "fields": []},
            {**valid, "fields": [field] * 11},
            {**valid, "fields": [field, field]},
            {**valid, "fields": [{**field, "label": ""}]},
            {**valid, "fields": [{**field, "required": 1}]},
            {**valid, "fields": [{**field, "max_length": 0}]},
            {**valid, "fields": [{**field, "max_length": 2001}]},
            {**valid, "fields": [{**field, "placeholder": "x" * 101}]},
            {**valid, "fields": [{**field, "max_length": 3, "value": "long"}]},
            {**valid, "fields": [{**field, "unknown": True}]},
        ]
        for component in invalid_components:
            with self.subTest(component=component):
                self.assertIsNone(PluginManager._validate_ui_definition(
                    plugin, self.definition(components=[component])
                ))

    def test_rejects_form_action_duplicates_and_button_collisions(self):
        form = self.form_component(action="submit")
        duplicate_forms = [
            self.definition("chat.toolbar", components=[form]),
            self.definition("settings.plugins", components=[
                self.form_component("other-form", action="submit")
            ]),
        ]
        collision = [
            self.definition("chat.toolbar", components=[form]),
            self.definition("settings.plugins", components=[{
                "type": "button", "id": "submit-button", "label": "Submit",
                "action": "submit",
            }]),
        ]
        plugin = self.plugin("demo", definition=duplicate_forms)

        self.assertIsNone(PluginManager._validate_ui_definitions(plugin, duplicate_forms))
        self.assertIsNone(PluginManager._validate_ui_definitions(plugin, collision))

    async def test_dispatches_valid_form_payload_and_preserves_values(self):
        form = self.form_component(fields=[
            {
                "id": "required", "label": "Required", "required": True,
                "max_length": 10, "placeholder": "", "value": "",
            },
            {
                "id": "optional", "label": "Optional", "required": False,
                "max_length": 10, "placeholder": "", "value": "",
            },
        ])
        definition = self.definition(components=[form])
        manager = self.manager([self.plugin("demo", definition=definition)])
        payload = {
            "form_id": "search-form",
            "values": {"required": "   ", "optional": ""},
        }

        result = await manager.dispatch_ui_action("demo", "search", payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], payload)

    async def test_validates_select_payload_against_published_options(self):
        select = {
            "type": "select", "id": "mode", "label": "Mode", "required": True,
            "options": [
                {"value": "safe", "label": "Safe"},
                {"value": "fast", "label": "Fast"},
            ],
            "value": "safe",
        }
        form = self.form_component(fields=[select])
        definition = self.definition(components=[form])
        manager = self.manager([self.plugin("demo", definition=definition)])

        accepted = await manager.dispatch_ui_action("demo", "search", {
            "form_id": "search-form", "values": {"mode": "fast"},
        })
        self.assertEqual(accepted["data"]["values"]["mode"], "fast")

        rejecting = self.manager([
            self.plugin("demo", definition=definition, error="action")
        ])
        with self.assertRaises(ValueError):
            await rejecting.dispatch_ui_action("demo", "search", {
                "form_id": "search-form", "values": {"mode": "injected"},
            })

    async def test_validates_checkbox_payload_type_and_required_semantics(self):
        fields = [
            {
                "id": "name", "label": "Name", "required": True,
                "max_length": 20, "placeholder": "", "value": "",
            },
            {
                "type": "checkbox", "id": "confirm", "label": "Confirm",
                "required": True, "value": False,
            },
            {
                "type": "checkbox", "id": "notify", "label": "Notify",
                "required": False, "value": False,
            },
        ]
        form = self.form_component(fields=fields)
        definition = self.definition(components=[form])
        manager = self.manager([self.plugin("demo", definition=definition)])
        payload = {
            "form_id": "search-form",
            "values": {"name": "Example", "confirm": True, "notify": False},
        }

        accepted = await manager.dispatch_ui_action("demo", "search", payload)

        self.assertEqual(accepted["data"], payload)
        optional_true = {
            **payload,
            "values": {**payload["values"], "notify": True},
        }
        accepted_optional = await manager.dispatch_ui_action(
            "demo", "search", optional_true
        )
        self.assertTrue(accepted_optional["data"]["values"]["notify"])
        invalid_values = [False, 0, 1, "true", None]
        for value in invalid_values:
            with self.subTest(value=value):
                rejecting = self.manager([
                    self.plugin("demo", definition=definition, error="action")
                ])
                invalid = {
                    **payload,
                    "values": {**payload["values"], "confirm": value},
                }
                with self.assertRaises(ValueError):
                    await rejecting.dispatch_ui_action("demo", "search", invalid)

    async def test_validates_number_payload_null_finite_and_bounds(self):
        number = {
            "type": "number", "id": "amount", "label": "Amount",
            "required": False, "min": 0, "max": 10, "value": None,
        }
        definition = self.definition(components=[self.form_component(fields=[number])])
        manager = self.manager([self.plugin("demo", definition=definition)])
        for value in (None, 0, 2.5, 10):
            result = await manager.dispatch_ui_action("demo", "search", {
                "form_id": "search-form", "values": {"amount": value},
            })
            self.assertEqual(result["data"]["values"]["amount"], value)
        for value in (True, "1", float("nan"), float("inf"), -1, 11):
            rejecting = self.manager([self.plugin("demo", definition=definition, error="action")])
            with self.assertRaises(ValueError):
                await rejecting.dispatch_ui_action("demo", "search", {
                    "form_id": "search-form", "values": {"amount": value},
                })
        required = {**number, "required": True}
        required_def = self.definition(components=[self.form_component(fields=[required])])
        required_manager = self.manager([self.plugin("required", definition=required_def)])
        with self.assertRaises(ValueError):
            await required_manager.dispatch_ui_action("required", "search", {
                "form_id": "search-form", "values": {"amount": None},
            })
    async def test_validates_secret_field_definition_and_registered_references(self):
        secret = self.secret_field()
        definition = self.definition(components=[self.form_component(fields=[secret])])
        manager = self.manager([self.plugin("demo", definition=definition)])
        manager.set_secret_validator(lambda reference: reference == "{{secret:7}}")

        accepted = await manager.dispatch_ui_action("demo", "search", {
            "form_id": "search-form", "values": {"token": "{{secret:7}}"},
        })
        self.assertEqual(accepted["data"]["values"]["token"], "{{secret:7}}")
        accepted_null = await manager.dispatch_ui_action("demo", "search", {
            "form_id": "search-form", "values": {"token": None},
        })
        self.assertIsNone(accepted_null["data"]["values"]["token"])

        for value in ("secret", "{{secret:8}}", "{{secret:7}}suffix", 7, True):
            with self.subTest(value=value):
                rejecting = self.manager([self.plugin("demo", definition=definition, error="action")])
                rejecting.set_secret_validator(lambda reference: reference == "{{secret:7}}")
                with self.assertRaises(ValueError):
                    await rejecting.dispatch_ui_action("demo", "search", {
                        "form_id": "search-form", "values": {"token": value},
                    })

        required = self.secret_field(required=True)
        required_definition = self.definition(components=[self.form_component(fields=[required])])
        required_manager = self.manager([self.plugin("required", definition=required_definition)])
        required_manager.set_secret_validator(lambda reference: reference == "{{secret:7}}")
        with self.assertRaises(ValueError):
            await required_manager.dispatch_ui_action("required", "search", {
                "form_id": "search-form", "values": {"token": None},
            })

        for malformed in (
            {**secret, "value": "{{secret:7}}"},
            {**secret, "placeholder": "x" * 101},
            {**secret, "placeholder": 1},
        ):
            with self.subTest(malformed=malformed):
                self.assertIsNone(PluginManager._validate_ui_definition(
                    self.plugin("demo"), self.definition(components=[self.form_component(fields=[malformed])])
                ))

    async def test_rejects_invalid_form_payload_before_plugin_handler(self):
        form = self.form_component()
        definition = self.definition(components=[form])
        invalid_payloads = [
            {"form_id": "search-form", "values": {"query": "ok"}, "extra": True},
            {"form_id": "wrong", "values": {"query": "ok"}},
            {"form_id": "search-form", "values": "not an object"},
            {"form_id": "search-form", "values": {}},
            {"form_id": "search-form", "values": {"query": "ok", "extra": "x"}},
            {"form_id": "search-form", "values": {"query": 1}},
            {"form_id": "search-form", "values": {"query": ""}},
            {"form_id": "search-form", "values": {"query": "x" * 201}},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                manager = self.manager([
                    self.plugin("demo", definition=definition, error="action")
                ])
                with self.assertRaises(ValueError):
                    await manager.dispatch_ui_action("demo", "search", payload)

    async def test_disabled_form_and_button_form_routing_are_isolated(self):
        disabled = self.definition(components=[
            self.form_component(disabled=True)
        ])
        manager = self.manager([self.plugin("demo", definition=disabled)])
        with self.assertRaises(KeyError):
            await manager.dispatch_ui_action("demo", "search", {
                "form_id": "search-form", "values": {"query": "ok"},
            })

        button_manager = self.manager([
            self.plugin("button", definition=self.definition())
        ])
        result = await button_manager.dispatch_ui_action("button", "run", {})
        self.assertEqual(result["status"], "ok")
        with self.assertRaises(KeyError):
            await manager.dispatch_ui_action("demo", "search", {})

    def test_accepts_all_four_ui_slots(self):
        plugin = self.plugin("demo", definition=self.definition())
        slots = [
            "chat.input_actions",
            "chat.toolbar",
            "studio.actions",
            "settings.plugins",
        ]

        for slot in slots:
            with self.subTest(slot=slot):
                definition = self.definition(slot)
                normalized = PluginManager._validate_ui_definition(plugin, definition)
                self.assertEqual(normalized["slot"], slot)

    def test_rejects_unknown_fields_types_names_and_duplicate_ids(self):
        plugin = self.plugin("demo", definition=self.definition())
        valid = self.definition()
        cases = [
            {**valid, "html": "<b>x</b>"},
            self.definition("sessions.actions"),
            self.definition(components=[{
                "type": "image", "id": "x", "label": "X", "action": "run",
            }]),
            self.definition(components=[{
                "type": "button", "id": "bad id", "label": "X", "action": "run",
            }]),
            self.definition(components=[{
                "type": "button", "id": "x", "label": "", "action": "run",
            }]),
            self.definition(components=[
                {"type": "button", "id": "x", "label": "X", "action": "one"},
                {"type": "button", "id": "x", "label": "Y", "action": "two"},
            ]),
        ]

        for definition in cases:
            with self.subTest(definition=definition):
                self.assertIsNone(
                    PluginManager._validate_ui_definition(plugin, definition)
                )

    def test_accepts_display_components_and_rejects_type_field_leaks(self):
        plugin = self.plugin("demo", definition=self.definition())
        valid = self.definition(components=[
            {"type": "separator", "id": "split"},
            {"type": "status", "id": "state", "text": " Ready ", "level": "success"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])

        normalized = PluginManager._validate_ui_definition(plugin, valid)

        self.assertEqual(normalized["components"], [
            {"type": "separator", "id": "split"},
            {"type": "status", "id": "state", "text": "Ready", "level": "success"},
            {"type": "button", "id": "run", "label": "Run", "action": "run", "disabled": False},
        ])
        boundary = self.definition(components=[
            {"type": "status", "id": "state", "text": "x" * 200, "level": "info"},
        ])
        self.assertIsNotNone(PluginManager._validate_ui_definition(plugin, boundary))

        invalid_components = [
            {"type": "separator", "id": "split", "text": "leak"},
            {"type": "status", "id": "state", "text": "Ready", "level": "debug"},
            {"type": "status", "id": "state", "text": "", "level": "info"},
            {"type": "status", "id": "state", "text": "x" * 201, "level": "info"},
            {"type": "status", "id": "state", "text": "Ready", "level": "info", "action": "run"},
        ]
        for component in invalid_components:
            with self.subTest(component=component):
                self.assertIsNone(PluginManager._validate_ui_definition(
                    plugin, self.definition(components=[component])
                ))

    async def test_display_components_do_not_expose_actions(self):
        definition = self.definition(components=[
            {"type": "separator", "id": "split"},
            {"type": "status", "id": "state", "text": "Ready", "level": "info"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])
        manager = self.manager([self.plugin("demo", definition=definition)])

        result = await manager.dispatch_ui_action("demo", "run", {})

        self.assertEqual(result["status"], "ok")
        with self.assertRaises(KeyError):
            await manager.dispatch_ui_action("demo", "state", {})

    async def test_dispatches_only_enabled_defined_actions(self):
        plugin = self.plugin("demo", definition=self.definition())
        manager = self.manager([plugin])

        result = await manager.dispatch_ui_action("demo", "run", {"value": 1})

        self.assertEqual(result, {
            "status": "ok",
            "message": "done",
            "data": {"value": 1},
        })
        for plugin_name, action in [
            ("missing", "run"),
            ("demo", "missing"),
            ("bad name", "run"),
        ]:
            with self.subTest(plugin=plugin_name, action=action):
                with self.assertRaises(KeyError):
                    await manager.dispatch_ui_action(plugin_name, action, {})

    async def test_disabled_actions_and_plugin_failures_are_isolated(self):
        disabled = self.definition(components=[{
            "type": "button",
            "id": "disabled",
            "label": "Disabled",
            "action": "run",
            "disabled": True,
        }])
        manager = self.manager([self.plugin("disabled", definition=disabled)])
        with self.assertRaises(KeyError):
            await manager.dispatch_ui_action("disabled", "run", {})

        broken = self.manager([
            self.plugin("broken", definition=self.definition(), error="action")
        ])
        with self.assertLogs("rp-standalone", level="ERROR"):
            result = await broken.dispatch_ui_action("broken", "run", {})
        self.assertEqual(
            result,
            {"status": "error", "message": "plugin action failed", "data": {}},
        )

    async def test_invalid_or_oversized_plugin_responses_are_replaced(self):
        cases = [
            {"status": "ok", "message": "x", "data": {"bad": {1, 2}}},
            {"status": "ok", "message": "x", "data": {"bad": float("nan")}},
            {"status": "ok", "message": "x", "data": {"large": "x" * 70_000}},
            {"status": "unknown", "message": "x", "data": {}},
            "not a dict",
        ]
        for result in cases:
            with self.subTest(result_type=type(result).__name__):
                manager = self.manager([
                    self.plugin("demo", definition=self.definition(), result=result)
                ])
                actual = await manager.dispatch_ui_action("demo", "run", {})
                self.assertEqual(
                    actual,
                    {
                        "status": "error",
                        "message": "invalid plugin response",
                        "data": {},
                    },
                )

    async def test_accepts_normalized_status_updates_and_preserves_other_data(self):
        definition = self.definition(components=[
            {"type": "status", "id": "state", "text": "Ready", "level": "info"},
            {"type": "status", "id": "detail", "text": "Idle", "level": "info"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])
        updates = [
            {"component_id": "state", "text": " Connected ", "level": "success"},
            {"component_id": "detail", "text": "Working", "level": "warning"},
        ]
        result = {"status": "ok", "message": "updated", "data": {
            "value": 1, "ui_updates": updates,
        }}
        manager = self.manager([self.plugin("demo", definition=definition, result=result)])

        actual = await manager.dispatch_ui_action("demo", "run", {})

        self.assertEqual(actual["data"]["value"], 1)
        self.assertEqual(actual["data"]["ui_updates"], [
            {"component_id": "state", "text": "Connected", "level": "success"},
            {"component_id": "detail", "text": "Working", "level": "warning"},
        ])

    async def test_accepts_omitted_empty_and_boundary_status_updates(self):
        definition = self.definition(components=[
            {"type": "status", "id": "state", "text": "Ready", "level": "info"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])
        cases = [
            {"value": 1},
            {"ui_updates": []},
            {"ui_updates": [{
                "component_id": "state", "text": "x" * 200, "level": "error",
            }]},
        ]
        for data in cases:
            with self.subTest(data=data):
                result = {"status": "ok", "message": "done", "data": data}
                manager = self.manager([
                    self.plugin("demo", definition=definition, result=result)
                ])
                actual = await manager.dispatch_ui_action("demo", "run", {})
                self.assertEqual(actual["status"], "ok")

    async def test_rejects_invalid_status_updates_all_or_nothing(self):
        definition = self.definition(components=[
            {"type": "separator", "id": "split"},
            {"type": "status", "id": "state", "text": "Ready", "level": "info"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])
        valid = {"component_id": "state", "text": "Updated", "level": "success"}
        invalid_updates = [
            "not a list",
            [valid] * 11,
            [{**valid, "html": "<b>x</b>"}],
            [{**valid, "component_id": "bad id"}],
            [{**valid, "component_id": "missing"}],
            [{**valid, "component_id": "run"}],
            [{**valid, "component_id": "split"}],
            [{**valid, "text": ""}],
            [{**valid, "text": "x" * 201}],
            [{**valid, "level": "debug"}],
            [valid, valid],
            [valid, {**valid, "component_id": "missing"}],
        ]
        invalid_response = {
            "status": "error", "message": "invalid plugin response", "data": {},
        }
        for updates in invalid_updates:
            with self.subTest(updates=updates):
                result = {"status": "ok", "message": "done", "data": {
                    "keep": True, "ui_updates": updates,
                }}
                manager = self.manager([
                    self.plugin("demo", definition=definition, result=result)
                ])
                actual = await manager.dispatch_ui_action("demo", "run", {})
                self.assertEqual(actual, invalid_response)

    async def test_cannot_update_another_plugins_status(self):
        demo_definition = self.definition(components=[
            {"type": "status", "id": "own", "text": "Ready", "level": "info"},
            {"type": "button", "id": "run", "label": "Run", "action": "run"},
        ])
        other_definition = self.definition(components=[
            {"type": "status", "id": "other", "text": "Ready", "level": "info"},
        ])
        result = {"status": "ok", "message": "done", "data": {"ui_updates": [{
            "component_id": "other", "text": "Changed", "level": "error",
        }]}}
        manager = self.manager([
            self.plugin("demo", definition=demo_definition, result=result),
            self.plugin("other", definition=other_definition),
        ])

        actual = await manager.dispatch_ui_action("demo", "run", {})

        self.assertEqual(actual, {
            "status": "error", "message": "invalid plugin response", "data": {},
        })

    async def test_default_action_handler_preserves_existing_plugin_compatibility(self):
        class ExistingPlugin(PluginBase):
            name = "existing"
            hooks = []

            async def run(self, hook, data, ctx):
                return None

        result = await ExistingPlugin().handle_ui_action("anything", {}, None)

        self.assertEqual(
            result,
            {"status": "error", "message": "unsupported action", "data": {}},
        )

    def test_api_and_frontend_enforce_limits_and_dom_only_rendering(self):
        main_source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        chat_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        studio_html = (ROOT / "frontend" / "studio.html").read_text(encoding="utf-8")
        settings_html = (ROOT / "frontend" / "settings.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "js" / "plugin-ui.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('@app.get("/api/plugins/ui")', main_source)
        self.assertIn('"version": 10', main_source)
        self.assertIn(
            '@app.post("/api/plugins/{plugin_name}/actions/{action}")',
            main_source,
        )
        self.assertIn("if not _same_origin(request):", main_source)
        self.assertIn("16_384", main_source)
        self.assertIn('persona_id=persona_manager.active or ""', main_source)
        self.assertIn('data-plugin-slot="chat.toolbar"', chat_html)
        self.assertIn('data-plugin-slot="chat.input_actions"', chat_html)
        self.assertIn('data-plugin-slot="studio.actions"', studio_html)
        self.assertIn('data-plugin-slot="settings.plugins"', settings_html)
        for html in (chat_html, studio_html, settings_html):
            self.assertIn('src="/frontend/js/plugin-ui.js"', html)
            self.assertLess(
                html.index('src="/frontend/js/i18n.js"'),
                html.index('src="/frontend/js/plugin-ui.js"'),
            )
            self.assertIn('id="plugin-ui-feedback"', html)
        self.assertIn('button.textContent = component.label', script)
        self.assertIn('status.textContent = component.text', script)
        self.assertIn('separator.setAttribute("role", "separator")', script)
        self.assertIn('payload.version !== 10', script)
        self.assertIn('const groups = new Map()', script)
        self.assertIn('if (!groups.has(pluginName)) groups.set(pluginName, [])', script)
        self.assertIn('function validPluginDefinitions(definitions)', script)
        self.assertIn('collectValidDefinitions(payload.plugins)', script)
        self.assertIn('function normalizeUiUpdates(updates)', script)
        self.assertIn('function applyUiUpdates(pluginName, updates)', script)
        self.assertIn('status.textContent = update.text', script)
        self.assertIn('status.classList.remove(...STATUS_CLASSES)', script)
        self.assertIn('button.disabled = component.disabled', script)
        self.assertIn('function requestAction(pluginName, action, payload)', script)
        self.assertIn('form.addEventListener("submit"', script)
        self.assertIn('event.preventDefault()', script)
        self.assertIn('input.type = "text"', script)
        self.assertIn('document.createElement("textarea")', script)
        self.assertIn('document.createElement("select")', script)
        self.assertIn('input.type = "checkbox"', script)
        self.assertIn('input.checked = field.value', script)
        self.assertIn('input.type === "checkbox"', script)
        self.assertIn('input.valueAsNumber', script)
        self.assertIn('input.type = "hidden"', script)
        self.assertIn('fetch("/api/secrets/register"', script)
        self.assertIn('SECRET_REFERENCE_RE.test(result.placeholder', script)
        self.assertIn('option.textContent = item.label', script)
        self.assertIn('input.autocomplete = "off"', script)
        self.assertIn('input.value = field.value', script)
        self.assertIn('form_id: component.id', script)
        self.assertIn('allControls.forEach(control => { control.disabled = true; })', script)
        self.assertIn('button.addEventListener("click"', script)
        self.assertIn("slot.replaceChildren()", script)
        self.assertIn("generation !== initGeneration", script)
        self.assertIn("console.error", script)
        self.assertNotIn("innerHTML", script)
        for html in (chat_html, studio_html, settings_html):
            for attribute in ("onclick=", "onchange=", "oninput=", " style="):
                self.assertNotIn(attribute, html)


class PluginDevelopmentGuideTests(unittest.IsolatedAsyncioTestCase):
    def test_guide_and_template_match_current_plugin_contract(self):
        guide = (ROOT / "document" / "plugin_development.md").read_text(
            encoding="utf-8"
        )
        template = ROOT / "backend" / "plugins" / "_template" / "plugin.py"
        source = template.read_text(encoding="utf-8")
        compile(source, str(template), "exec")

        for required in [
            "version 10",
            "chat.input_actions",
            "chat.toolbar",
            "studio.actions",
            "settings.plugins",
            "{form_id, values}",
            "機密値",
            "backend/config.yaml",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, guide)
        self.assertIn("class TemplatePlugin(PluginBase):", source)
        self.assertIn("async def run(self, hook: str, data, ctx):", source)
        self.assertIn("def get_ui_slot(self) -> dict | list[dict] | None:", source)

        for config_name in ("config.yaml", "config.default.yaml"):
            config = (ROOT / "backend" / config_name).read_text(encoding="utf-8")
            self.assertNotIn("- _template", config)

    async def test_template_ui_and_actions_pass_real_manager_validation(self):
        from plugins._template.plugin import TemplatePlugin

        plugin = TemplatePlugin()
        definitions = PluginManager._validate_ui_definitions(
            plugin, plugin.get_ui_slot()
        )
        self.assertIsNotNone(definitions)
        self.assertEqual(len(definitions), 2)

        manager = PluginManager.__new__(PluginManager)
        manager.plugins = [plugin]
        refreshed = await manager.dispatch_ui_action("my_plugin", "refresh", {})
        self.assertEqual(refreshed["status"], "ok")
        self.assertEqual(
            refreshed["data"]["ui_updates"][0]["component_id"], "state"
        )

        saved = await manager.dispatch_ui_action("my_plugin", "save_settings", {
            "form_id": "settings-form",
            "values": {
                "display_name": "Example", "mode": "safe", "enabled": False,
                "limit": None,
            },
        })
        self.assertEqual(saved["status"], "ok")


class ConfigValidationTests(unittest.TestCase):
    def test_valid_settings_are_normalized(self):
        self.assertEqual(
            validate_api_settings({"temperature": 1}), {"temperature": 1.0}
        )
        self.assertEqual(
            validate_session_settings({"save_interval": 2}), {"save_interval": 2}
        )
        self.assertEqual(
            validate_style_settings({"narration": False}), {"narration": False}
        )
        watchdog = validate_watchdog_settings({
            "enabled": True,
            "check_interval": 60,
            "levels": [{"after": 300, "subject": "s", "body": "b"}],
        })
        self.assertEqual(watchdog["levels"][0]["after"], 300)

    def test_invalid_types_ranges_and_unknown_keys_are_rejected(self):
        invalid = [
            lambda: validate_api_settings({"max_tokens": True}),
            lambda: validate_api_settings({"temperature": float("nan")}),
            lambda: validate_api_settings({"timeout": 601}),
            lambda: validate_session_settings({"save_interval": 0}),
            lambda: validate_style_settings({"narration": "false"}),
            lambda: validate_watchdog_settings({"check_interval": 1}),
            lambda: validate_api_settings({"unexpected": 1}),
        ]
        for validate in invalid:
            with self.subTest(validate=validate):
                with self.assertRaises(ValueError):
                    validate()

    def test_persona_id_uses_dataset_property(self):
        source = (ROOT / "frontend" / "js" / "session-setup.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("card.dataset.id = String(persona.id ?? '')", source)
        self.assertNotIn('data-id="${', source)


class HistoryTests(unittest.TestCase):
    def test_save_interval_batches_pending_turns_and_force_flushes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            history = History(root, "persona", save_interval=2)
            history.set_session_id("12345678", "2026-07-15")

            history.add("u1", "a1")
            history.save_turn()
            self.assertFalse(history.session_file.exists())

            history.add("u2", "a2")
            history.save_turn()
            rows = [json.loads(line) for line in history.session_file.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(
                [row["content"] for row in rows],
                ["u1", "a1", "u2", "a2"],
            )

            history.add("u3", "a3")
            history.save_turn(force=True)
            rows = [json.loads(line) for line in history.session_file.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(
                [row["content"] for row in rows[-2:]], ["u3", "a3"])

    def test_resume_keeps_full_history_but_context_uses_complete_recent_turns(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            persona_dir = root / "persona"
            persona_dir.mkdir()
            session_file = persona_dir / "2026-07-15_12345678.jsonl"
            rows = []
            for n in range(3):
                rows.extend([
                    {"role": "user", "content": f"u{n}" + "x" * 40},
                    {"role": "assistant", "content": f"a{n}" + "y" * 40},
                ])
            session_file.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            history = History(root, "persona", max_tokens=100)
            history.set_session_id("12345678", "2026-07-15")
            history._load_specific(session_file)

            self.assertEqual(len(history._messages), 6)
            self.assertEqual(history._turn_count, 3)
            context = history.get_context()
            self.assertEqual(context, rows[-2:])
            self.assertEqual([m["role"] for m in context], ["user", "assistant"])


class PersonaStyleTests(unittest.TestCase):
    def test_global_style_is_base_and_persona_style_overrides_it(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            persona = root / "persona"
            persona.mkdir()
            (persona / "style.yaml").write_text(
                "style:\n  narration: false\n", encoding="utf-8")
            manager = PersonaManager(
                root,
                "persona",
                default_style={
                    "viewpoint": "user_character",
                    "person": "third",
                    "narration": True,
                },
            )
            manager.ensure_active()

            style = manager.start_session()

            self.assertEqual(style, {
                "viewpoint": "user_character",
                "person": "third",
                "narration": False,
            })


class SessionLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_log_is_overwritten_idempotently(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            history = History(root / "sessions", "persona")
            history.set_session_id("12345678", "2026-07-15")
            history.add("hello", "world")
            ctx = type("Context", (), {"history": history, "persona_id": "persona"})()
            plugin = SessionLogPlugin()
            plugin.set_log_dir(root / "logs")

            await plugin._write_log(ctx)
            await plugin._write_log(ctx)

            content = (root / "logs" / "persona" / "2026-07-15_12345678.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(content.count("# Session Log"), 1)


class MemoryDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_and_batch_deduplication_are_conservative(self):
        facts = [
            "・  ユーザーはＡＢＣが好きである",
            "1. ユーザーはABCが好きである",
            "2026年に東京へ行った",
            "2026年に大阪へ行った",
        ]

        unique = deduplicate_facts(facts)

        self.assertEqual(unique, [
            "ユーザーはABCが好きである",
            "2026年に東京へ行った",
            "2026年に大阪へ行った",
        ])
        self.assertEqual(normalize_fact("  *  複数\n 空白  "), "複数 空白")
        self.assertEqual(
            fact_id("persona", "session", unique[0]),
            fact_id("persona", "session", facts[0]),
        )
        self.assertNotEqual(
            fact_id("persona", "session-a", unique[0]),
            fact_id("persona", "session-b", unique[0]),
        )

    async def test_store_facts_excludes_legacy_and_batch_duplicates(self):
        collection = mock.MagicMock()
        collection.get.return_value = {
            "documents": ["ユーザーは紅茶が好きである"],
        }
        embedding = mock.MagicMock()
        embedding.encode.return_value = [[0.1, 0.2]]
        plugin = MemoryPlugin()
        plugin._collection = collection
        plugin._embedding_provider = embedding

        stored = await plugin._store_facts(
            [
                "・ ユーザーは紅茶が好きである",
                "1. ユーザーは珈琲が好きである",
                "ユーザーは珈琲が好きである",
            ],
            "persona",
            "session",
        )

        self.assertEqual(stored, 1)
        collection.get.assert_called_once_with(
            where={"$and": [
                {"persona_id": "persona"},
                {"session_id": "session"},
            ]},
            include=["documents"],
        )
        embedding.encode.assert_called_once_with(["ユーザーは珈琲が好きである"])
        collection.upsert.assert_called_once()
        kwargs = collection.upsert.call_args.kwargs
        self.assertEqual(kwargs["documents"], ["ユーザーは珈琲が好きである"])
        self.assertEqual(
            kwargs["ids"],
            [fact_id("persona", "session", "ユーザーは珈琲が好きである")],
        )

    async def test_all_duplicates_skip_embedding_and_upsert(self):
        collection = mock.MagicMock()
        collection.get.return_value = {
            "documents": ["ユーザーは紅茶が好きである"],
        }
        embedding = mock.MagicMock()
        plugin = MemoryPlugin()
        plugin._collection = collection
        plugin._embedding_provider = embedding

        stored = await plugin._store_facts(
            ["1. ユーザーは紅茶が好きである"],
            "persona",
            "session",
        )

        self.assertEqual(stored, 0)
        embedding.encode.assert_not_called()
        collection.upsert.assert_not_called()

    async def test_lookup_failure_still_uses_deterministic_upsert(self):
        collection = mock.MagicMock()
        collection.get.side_effect = RuntimeError("lookup failed")
        embedding = mock.MagicMock()
        embedding.encode.return_value = [[0.1]]
        plugin = MemoryPlugin()
        plugin._collection = collection
        plugin._embedding_provider = embedding

        with self.assertLogs("rp-standalone", level="ERROR"):
            stored = await plugin._store_facts(
                ["ユーザーは紅茶が好きである"],
                "persona",
                "session",
            )

        self.assertEqual(stored, 1)
        collection.upsert.assert_called_once()


class SecretsTests(unittest.TestCase):
    def test_register_normalize_reveal_and_atomic_save(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            store = Path(tmp) / "secrets_store.json"
            plugin = SecretsPlugin()
            plugin.configure(str(store))

            normalized = plugin.normalize_text("勤務先は {{s: workplace: Example Inc.}} です")

            self.assertEqual(normalized, "勤務先は {{secret:1}} です")
            self.assertEqual(plugin.reveal("{{secret:1}}"), "Example Inc.")
            self.assertEqual(plugin.get_entry("prefix {{secret:1}}"), None)
            saved = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(saved["secrets"]["1"]["label"], "workplace")
            self.assertFalse(list(store.parent.glob("*.tmp")))

    @mock.patch("plugins.secrets.plugin.os.chmod")
    @mock.patch.object(SecretsPlugin, "_supports_posix_permissions", return_value=True)
    def test_configure_restricts_existing_store_before_load(
        self, _supports_mock, chmod_mock
    ):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            store = Path(tmp) / "secrets_store.json"
            store.write_text('{"secrets": {}, "next_id": 1}', encoding="utf-8")
            plugin = SecretsPlugin()
            plugin.configure(str(store))
            chmod_mock.assert_called_once_with(store, 0o600)

    @mock.patch.object(SecretsPlugin, "_supports_posix_permissions", return_value=True)
    def test_configure_propagates_permission_failure(self, _supports_mock):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            store = Path(tmp) / "secrets_store.json"
            store.write_text('{"secrets": {}, "next_id": 1}', encoding="utf-8")
            plugin = SecretsPlugin()
            with mock.patch(
                "plugins.secrets.plugin.os.chmod",
                side_effect=PermissionError("denied"),
            ):
                with self.assertRaises(PermissionError):
                    plugin.configure(str(store))

    @mock.patch.object(SecretsPlugin, "_supports_posix_permissions", return_value=True)
    def test_posix_temp_is_created_private(self, _supports_mock):
        file_handle = mock.MagicMock()
        context = mock.MagicMock()
        context.__enter__.return_value = file_handle
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        with (
            mock.patch("plugins.secrets.plugin.os.open", return_value=42) as open_mock,
            mock.patch("plugins.secrets.plugin.os.fchmod", create=True) as fchmod_mock,
            mock.patch("plugins.secrets.plugin.os.fdopen", return_value=context),
        ):
            SecretsPlugin._write_private_text(Path("secret.tmp"), "secret")

        open_mock.assert_called_once_with(Path("secret.tmp"), flags, 0o600)
        fchmod_mock.assert_called_once_with(42, 0o600)
        file_handle.write.assert_called_once_with("secret")

    @mock.patch("plugins.secrets.plugin.os.chmod")
    @mock.patch.object(SecretsPlugin, "_supports_posix_permissions", return_value=False)
    def test_windows_skips_posix_permission_changes(self, _supports_mock, chmod_mock):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            store = Path(tmp) / "secrets_store.json"
            plugin = SecretsPlugin()
            plugin.configure(str(store))
            plugin.register("private-value", "test")
            chmod_mock.assert_not_called()
            self.assertTrue(store.exists())

    def test_user_message_hook_masks_previously_registered_value(self):
        plugin = SecretsPlugin()
        plugin._secrets = {"7": {"value": "private-value", "label": "test"}}
        ctx = type("Context", (), {"user_input": "value=private-value"})()

        result = plugin._mask_input(ctx)

        self.assertIs(result, ctx)
        self.assertEqual(ctx.user_input, "value={{secret:7}}")

    def test_context_leak_is_replaced(self):
        plugin = SecretsPlugin()
        plugin._secrets = {"7": {"value": "private-value", "label": "test"}}
        messages = [{"role": "user", "content": "value=private-value"}]

        result = plugin._check_leak(messages)

        self.assertEqual(result[0]["content"], "value={{secret:7}}")

    def test_short_values_require_explicit_secret_syntax(self):
        plugin = SecretsPlugin()
        plugin._secrets = {
            "1": {"value": "A", "label": "one"},
            "2": {"value": "AB", "label": "two"},
            "3": {"value": "ABC", "label": "three"},
        }

        protected = plugin.protect_text("A AB ABC")

        self.assertEqual(protected, "A AB {{secret:3}}")
        self.assertEqual(plugin.protect_text("{{s: X}}"), "{{secret:1}}")

    def test_persona_studio_llm_messages_are_sanitized(self):
        secrets = SecretsPlugin()
        secrets._secrets = {"2": {"value": "private-value", "label": "test"}}
        studio = PersonaStudioPlugin()
        studio.set_secret_filter(secrets.protect_text)

        result = studio._sanitize_messages([
            {"role": "user", "content": "private-value and {{s: other}}"}
        ])

        self.assertEqual(result[0]["content"], "{{secret:2}} and {{secret:1}}")

    def test_frontend_keeps_placeholder_as_raw_message_data(self):
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertIn("textEl.dataset.rawText = raw", source)
        self.assertIn('body: JSON.stringify({ placeholder: token.dataset.placeholder })', source)
        self.assertIn("renderMessageText(currentDiv.querySelector(\".text\"), assistantText, true)", source)
        studio_source = (ROOT / "frontend" / "js" / "studio.js").read_text(encoding="utf-8")
        self.assertIn("secrets: studioSecretData()", studio_source)
        self.assertIn("restoreStudioSecrets(d.secrets || []);", studio_source)
        self.assertNotIn("studioSecrets.push({ label: data.label || label, placeholder: data.placeholder, value", studio_source)

    def test_chat_history_uses_masked_hook_input(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('history.add(ctx.user_input, "")', source)
        self.assertNotIn('history.add(user_text, "")', source)
        self.assertIn("content = _protect_secret_data(req.content)", source)
        self.assertNotIn("content = req.content", source)

class StateHistoryContractTests(unittest.TestCase):
    def test_state_snapshots_follow_history_edits(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('_state_history.jsonl', source)
        self.assertIn('def _record_state_snapshot(state: dict)', source)
        self.assertIn('if count == 0 or count % 2: return', source)
        self.assertIn('if item["message_count"] <= message_count', source)
        self.assertIn('state_history_path.unlink(missing_ok=True)', source)
        self.assertIn('state = _restore_state_for_history(len(history._messages))', source)

    def test_chat_refreshes_state_after_history_changes(self):
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertIn('async function refreshStatePanel()', source)
        self.assertGreaterEqual(source.count('await refreshStatePanel();'), 4)

class FrontendXssTests(unittest.TestCase):
    def test_external_values_use_dom_properties_and_listeners(self):
        js_dir = ROOT / "frontend" / "js"
        chat = (js_dir / "chat.js").read_text(encoding="utf-8")
        setup = (js_dir / "session-setup.js").read_text(encoding="utf-8")
        sessions = (js_dir / "sessions.js").read_text(encoding="utf-8")
        settings = (js_dir / "settings.js").read_text(encoding="utf-8")
        studio = (js_dir / "studio.js").read_text(encoding="utf-8")

        self.assertNotIn("li.innerHTML", chat)
        self.assertIn('text.textContent = String(p.label ?? "")', chat)
        self.assertNotIn("label.innerHTML", setup)
        self.assertIn("preview.textContent = JSON.stringify(est, null, 2)", setup)
        self.assertNotIn('onclick="continueSession', sessions)
        self.assertIn("continueBtn.addEventListener('click'", sessions)
        self.assertNotIn('onclick="editPersonaStyle', settings)
        self.assertIn("summary.textContent", settings)
        self.assertNotIn("${data.error}", studio)
        self.assertIn("error.textContent = String(data.error)", studio)
        self.assertNotIn('onclick="${onClick}', studio)
        self.assertIn('card.addEventListener("click", loadPersona)', studio)

    def test_frontend_avoids_inner_html_and_inline_script_handlers(self):
        frontend = ROOT / "frontend"
        js_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (frontend / "js").glob("*.js")
        )
        html_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in frontend.glob("*.html")
        }

        self.assertNotIn(".innerHTML", js_source)
        for source in html_sources.values():
            for attribute in ("onclick=", "ondblclick=", "onchange=", "oninput="):
                self.assertNotIn(attribute, source)
            self.assertNotIn("<script>", source)
            self.assertNotIn(' style=', source)

class CspPolicyTests(unittest.TestCase):
    def test_enforced_policy_and_report_endpoint_are_configured(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

        self.assertIn('response.headers["Content-Security-Policy"]', source)
        self.assertIn('"script-src \'self\'; "', source)
        self.assertIn('"style-src \'self\'; "', source)
        self.assertNotIn('"style-src \'self\' \'unsafe-inline\'; "', source)
        self.assertIn('"report-uri /api/csp-report"', source)
        self.assertIn('@app.post("/api/csp-report", status_code=204)', source)
        self.assertIn('int(content_length) > 16_384', source)
        self.assertIn('len(body) > 16_384', source)
        self.assertIn('parsed.path[:512]', source)


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_end_does_not_permanently_stop_monitor(self):
        plugin = WatchdogPlugin()
        plugin.configure({"enabled": True, "check_interval": 3600, "levels": []})
        await plugin.initialize()
        task = plugin._task
        await plugin.run("on_session_end", None, None)
        self.assertIs(plugin._task, task)
        self.assertFalse(task.done())
        await plugin.run("on_session_start", None, None)
        self.assertGreater(plugin._last_activity, 0)
        await plugin.shutdown()


if __name__ == "__main__":
    unittest.main()
