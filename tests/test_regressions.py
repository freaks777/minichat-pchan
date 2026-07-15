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

from core.api import _anthropic_messages, _gemini_contents
from core.config import (
    validate_api_settings,
    validate_session_settings,
    validate_style_settings,
    validate_watchdog_settings,
)
from core.history import History
from core.persona_manager import PersonaManager
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
