import ast
import asyncio
import json
import os
import re
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
    MEMORY_KIND_LEGACY,
    MEMORY_KIND_PERSONA_BASE,
    MEMORY_KIND_SESSION_FACT,
    MemoryPlugin,
    deduplicate_facts,
    fact_id,
    normalize_fact,
    persona_base_id,
)
from plugins.session_log.plugin import SessionLogPlugin
from plugins.secrets.plugin import SecretsPlugin
from plugins.persona_studio.plugin import PersonaStudioPlugin
from plugins.watchdog.plugin import WatchdogPlugin


class BootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.spec_from_file_location("rp_bootstrap", ROOT / "bootstrap.py")
        cls.bootstrap_module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.bootstrap_module)

    def _layout(self, root: Path):
        backend = root / "backend"
        backend.mkdir()
        (backend / "config.default.yaml").write_text("default: true", encoding="utf-8")
        (root / "requirements.txt").write_text("example-package", encoding="utf-8")

    def test_config_is_created_once_and_existing_content_is_untouched(self):
        module = self.bootstrap_module
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            self._layout(root)
            config = module.ensure_config(root)
            self.assertEqual(config.read_text(encoding="utf-8"), "default: true")

            config.write_text("# unique user config", encoding="utf-8")
            before = (config.read_bytes(), config.stat().st_mtime_ns)
            module.ensure_config(root)
            after = (config.read_bytes(), config.stat().st_mtime_ns)
            self.assertEqual(after, before)

    def test_empty_existing_config_is_rejected_without_overwrite(self):
        module = self.bootstrap_module
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            self._layout(root)
            config = root / "backend" / "config.yaml"
            config.write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "config is empty"):
                module.ensure_config(root)
            self.assertEqual(config.read_bytes(), b"")

    def test_missing_venv_is_created_installed_and_not_rebuilt(self):
        module = self.bootstrap_module
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            self._layout(root)
            calls = []

            def fake_runner(command, check):
                calls.append(command)
                if command[1:3] == ["-m", "venv"]:
                    python_path = module.venv_python_path(Path(command[3]))
                    python_path.parent.mkdir(parents=True)
                    python_path.write_text("", encoding="utf-8")

            python_path = module.ensure_venv(root, runner=fake_runner, executable="bootstrap-python")
            self.assertTrue(python_path.is_file())
            self.assertEqual(calls[0][0:3], ["bootstrap-python", "-m", "venv"])
            self.assertEqual(calls[1][1:4], ["-m", "pip", "install"])
            self.assertTrue((root / ".venv" / ".rp-bootstrap-complete").is_file())
            self.assertFalse((root / ".venv" / ".rp-bootstrap-incomplete").exists())

            second_runner = mock.Mock()
            module.ensure_venv(root, runner=second_runner)
            second_runner.assert_not_called()

    def test_python_version_and_launcher_contracts(self):
        module = self.bootstrap_module
        with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
            module.require_supported_python((3, 10, 9))

        bat = (ROOT / "start_server.bat").read_text(encoding="utf-8")
        shell = (ROOT / "start_server.sh").read_text(encoding="utf-8")
        self.assertIn("bootstrap.py", bat)
        self.assertIn("Scripts", bat)
        self.assertIn("sys.version_info < (3, 11)", bat)
        self.assertIn("bootstrap.py", shell)
        self.assertIn(".venv/bin/python", shell)
        self.assertIn("sys.version_info < (3, 11)", shell)
        self.assertNotIn("E:", bat)
        default_config = (ROOT / "backend" / "config.default.yaml").read_text(encoding="utf-8")
        self.assertNotIn("E:/", default_config)

    def test_persona_import_ui_requires_successful_validation(self):
        html = (ROOT / "frontend" / "studio.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "js" / "studio.js").read_text(encoding="utf-8")
        i18n = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('id="studio-import" class="btn btn-primary" data-i18n="btnImport" disabled', html)
        self.assertIn("setFileImportReady(false)", script)
        self.assertIn('data.error === "incomplete_persona"', script)
        self.assertIn('data.error === "invalid_persona_file"', script)
        self.assertIn('data.error === "persona_exists"', script)
        self.assertNotIn("不足分は自動生成", html + i18n)
        self.assertNotIn("auto-generated on import", i18n)

class PhaseCLegacySessionContractTests(unittest.TestCase):
    def test_runtime_session_apis_accept_only_canonical_filenames(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            're.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}\.jsonl", f.name)',
            source,
        )
        self.assertIn(
            're.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", file_stem)',
            source,
        )
        self.assertIn(
            're.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", date)',
            source,
        )
        self.assertNotIn('YYYY-MM-DD.jsonl', source)

    def test_documents_withdraw_legacy_compatibility_without_migration(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        design = (
            ROOT / "document" / "RPスタンドアロンアプリ_設計書.md"
        ).read_text(encoding="utf-8")
        changelog = (ROOT / "document" / "CHANGELOG.md").read_text(encoding="utf-8")
        backlog = (ROOT / "document" / "backlog.md").read_text(encoding="utf-8")

        self.assertIn("YYYY-MM-DD_HHMMSSRR.jsonl", readme)
        self.assertIn("互換・migration対象ではありません", readme)
        self.assertIn("runtime互換・migration・自動削除を提供しない", design)
        self.assertIn("旧session互換保証の撤回", changelog)
        self.assertIn("互換保証撤回・migrationなし", backlog)
        self.assertNotIn("_load_latest()", readme + design)

    def test_phase_c_adds_no_automatic_legacy_data_mutation(self):
        bootstrap = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("YYYY-MM-DD.jsonl", bootstrap + source)
        self.assertNotIn("legacy session migration", bootstrap.lower() + source.lower())

class PhaseBApiBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main as app_main
        from fastapi.testclient import TestClient

        cls.app_main = app_main
        cls.client = TestClient(app_main.app, base_url="http://127.0.0.1:8765")

    def test_chat_request_is_strict_and_bounded(self):
        from pydantic import ValidationError

        request = self.app_main.ChatRequest(
            text="  hello  ",
            persona_id="kyouka-detective",
            session_id="2026-07-17_12345678",
            resend=True,
        )
        self.assertEqual(request.text, "hello")
        invalid_payloads = [
            {"text": 123},
            {"text": {"nested": True}},
            {"text": "hello", "resend": "false"},
            {"text": "hello", "resend": 1},
            {"text": "hello", "extra": True},
            {"text": "hello", "persona_id": "../bad"},
            {"text": "hello", "session_id": "bad"},
            {"text": "   "},
            {"text": "x" * 8001},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.app_main.ChatRequest(**payload)

    def test_chat_body_limit_accepts_16384_and_rejects_16385_bytes(self):
        headers = {"content-type": "application/json"}
        accepted = self.client.post(
            "/api/chat",
            content=b"{" + (b" " * 16_383),
            headers=headers,
        )
        self.assertEqual(accepted.status_code, 422)

        rejected = self.client.post(
            "/api/chat",
            content=b"{" + (b" " * 16_384),
            headers=headers,
        )
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(rejected.json(), {"error": "payload_too_large"})

        invalid_length = self.client.post(
            "/api/chat",
            content=b"{}",
            headers={**headers, "content-length": "invalid"},
        )
        self.assertEqual(invalid_length.status_code, 413)

        actual_body_over = self.client.post(
            "/api/chat",
            content=b"{" + (b" " * 16_384),
            headers={**headers, "content-length": "16384"},
        )
        self.assertEqual(actual_body_over.status_code, 413)

    def test_global_same_origin_guard_covers_every_unsafe_api_route(self):
        unsafe = []
        replacements = {
            "plugin_name": "demo",
            "action": "run",
            "persona_id": "alice",
            "date": "2026-07-17_12345678",
        }
        for route in self.app_main.app.routes:
            path = getattr(route, "path", "")
            for method in sorted(set(getattr(route, "methods", set())) & {"POST", "PUT", "PATCH", "DELETE"}):
                if path.startswith("/api/"):
                    unsafe.append((method, path))

        self.assertEqual(len(unsafe), 39)
        for method, path in unsafe:
            resolved = re.sub(
                r"\{([^}]+)\}",
                lambda match: replacements.get(match.group(1), "value"),
                path,
            )
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    resolved,
                    content=b"{}",
                    headers={
                        "content-type": "application/json",
                        "origin": "https://attacker.example",
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"error": "cross_origin_forbidden"})

    def test_same_origin_missing_origin_and_fetch_metadata_contract(self):
        self.assertEqual(self.client.post("/api/chat/cancel").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/chat/cancel",
                headers={"origin": "http://127.0.0.1:8765"},
            ).status_code,
            200,
        )
        for headers in (
            {"origin": "null"},
            {"origin": "http://localhost:8765"},
            {"sec-fetch-site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                response = self.client.post("/api/chat/cancel", headers=headers)
                self.assertEqual(response.status_code, 403)

        from fastapi.testclient import TestClient
        localhost = TestClient(self.app_main.app, base_url="http://localhost:8765")
        self.assertEqual(
            localhost.post(
                "/api/chat/cancel",
                headers={"origin": "http://localhost:8765"},
            ).status_code,
            200,
        )
        non_loopback = TestClient(self.app_main.app, base_url="http://example.test:8765")
        self.assertEqual(non_loopback.post("/api/chat/cancel").status_code, 403)

    def test_history_frontend_retries_resume_only_once(self):
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertIn("if (res.status === 409 && allowResume)", source)
        self.assertEqual(source.count("return requestHistory(false)"), 1)
        self.assertIn('fetch("/api/session/resume"', source)
        self.assertIn("resend: !!textOverride", source)


class PhaseBAutoResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import main as app_main
        self.app_main = app_main

    async def test_chat_sse_preserves_normal_and_resend_paths(self):
        app_main = self.app_main

        async def fake_chat_stream(messages, config, model_info):
            yield "assistant reply"

        async def dispatch(hook, *args):
            if hook in {"on_build_context", "on_before_request"}:
                return args[0]
            return args[0] if args else None

        for resend in (False, True):
            with self.subTest(resend=resend):
                history = mock.Mock()
                history._messages = (
                    [{"role": "user", "content": "hello"}] if resend else []
                )
                history.add.side_effect = lambda user, assistant: history._messages.extend([
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": assistant},
                ])
                history.get_context.side_effect = lambda: list(history._messages)
                history.today_file = Path("session.jsonl")
                persona_manager = mock.Mock()
                persona_manager.active = "persona"
                persona_manager.get_active_style.return_value = {}
                plugin_manager = mock.Mock()
                plugin_manager.dispatch = mock.AsyncMock(side_effect=dispatch)
                plugin_manager.has.return_value = False
                state_tracking = mock.Mock()
                state_tracking.missing_count = 1

                with mock.patch.object(app_main, "history", history), mock.patch.object(
                    app_main, "persona_manager", persona_manager
                ), mock.patch.object(app_main, "plugin_manager", plugin_manager), mock.patch.object(
                    app_main, "chat_stream", fake_chat_stream
                ), mock.patch.object(
                    app_main, "_auto_resume_session", mock.AsyncMock(return_value=None)
                ), mock.patch.object(app_main, "rebuild_system_prompt"), mock.patch.object(
                    app_main, "_get_current_memory_scope", return_value="session"
                ), mock.patch.object(app_main, "_state_tracking", state_tracking), mock.patch.object(
                    app_main, "touch_last_response"
                ), mock.patch.object(app_main, "_api_lock", asyncio.Lock()), mock.patch.object(
                    app_main, "_cancel_event", asyncio.Event()
                ), mock.patch.object(
                    app_main, "config", {"active_model": "test", "active_provider": "test"}
                ):
                    response = await app_main.chat_sse(app_main.ChatRequest(
                        text="hello", persona_id="persona", session_id="12345678", resend=resend,
                    ))
                    chunks = []
                    async for chunk in response.body_iterator:
                        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)

                body = "".join(chunks)
                events = [
                    json.loads(line[6:])
                    for line in body.splitlines()
                    if line.startswith("data: ")
                ]
                content = "".join(
                    event.get("content", "") for event in events if event.get("type") == "chunk"
                )
                self.assertEqual(content, "assistant reply")
                self.assertIn('"type": "done"', body)
                self.assertEqual(history._messages, [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "assistant reply"},
                ])
                if resend:
                    history.add.assert_not_called()
                else:
                    history.add.assert_called_once_with("hello", "")

    async def _assert_preflight_rejected(self, root: Path, persona_id: str, session_id: str):
        app_main = self.app_main
        current_path = root / "backend" / ".current-session"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text('{"sentinel": true}', encoding="utf-8")
        before_state = current_path.read_bytes()

        persona_manager = mock.Mock()
        persona_manager.active = "current"
        history = mock.Mock()
        history.session_id = "11111111"
        history._session_date = "2026-07-17"
        history._messages = [{"role": "user", "content": "unchanged"}]
        before_messages = list(history._messages)

        with mock.patch.object(app_main, "BASE_DIR", root / "backend"), mock.patch.object(
            app_main, "PERSONAS_DIR", root / "personas"
        ), mock.patch.object(app_main, "persona_manager", persona_manager), mock.patch.object(
            app_main, "history", history
        ), mock.patch.object(
            app_main, "_dispatch_session_end_for_active", mock.AsyncMock()
        ) as end_mock, mock.patch.object(app_main, "_activate_session") as activate_mock:
            error = await app_main._auto_resume_session(persona_id, session_id)

        self.assertIsNotNone(error)
        self.assertEqual(persona_manager.active, "current")
        self.assertEqual(history._messages, before_messages)
        self.assertEqual(current_path.read_bytes(), before_state)
        end_mock.assert_not_awaited()
        activate_mock.assert_not_called()

    async def test_preflight_failures_do_not_end_or_change_current_session(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            personas = root / "personas"
            sessions = root / "sessions"
            (personas / "target").mkdir(parents=True)
            (sessions / "target").mkdir(parents=True)

            await self._assert_preflight_rejected(root, "../bad", "12345678")
            await self._assert_preflight_rejected(root, "missing", "12345678")
            await self._assert_preflight_rejected(root, "target", "bad")
            await self._assert_preflight_rejected(root, "target", "2026-07-17_12345678")
            await self._assert_preflight_rejected(root, "target", "12345678")

            corrupt = sessions / "target" / "2026-07-17_12345678.jsonl"
            corrupt.write_text("{broken json", encoding="utf-8")
            await self._assert_preflight_rejected(root, "target", "2026-07-17_12345678")
            corrupt.unlink()

            for date in ("2026-07-17", "2026-07-18"):
                (sessions / "target" / f"{date}_12345678.jsonl").write_text(
                    '{"role":"user","content":"ok"}\n', encoding="utf-8"
                )
            await self._assert_preflight_rejected(root, "target", "12345678")

    async def test_success_order_is_end_activate_start(self):
        app_main = self.app_main
        events = []
        target = app_main._ResolvedSessionTarget(
            persona_id="target",
            session_id="12345678",
            session_date="2026-07-17",
            jsonl_path=Path("session.jsonl"),
            messages=[{"role": "user", "content": "hello"}],
        )
        persona_manager = mock.Mock()
        persona_manager.active = "current"
        persona_manager.get_active_style.return_value = {}
        history = mock.Mock()
        history.session_id = "11111111"
        history._session_date = "2026-07-17"
        plugin_manager = mock.Mock()
        plugin_manager.dispatch = mock.AsyncMock(
            side_effect=lambda hook, *args: events.append("start")
        )

        with mock.patch.object(app_main, "persona_manager", persona_manager), mock.patch.object(
            app_main, "history", history
        ), mock.patch.object(
            app_main, "plugin_manager", plugin_manager
        ), mock.patch.object(
            app_main, "_resolve_session_target", return_value=target
        ), mock.patch.object(
            app_main, "_dispatch_session_end_for_active",
            mock.AsyncMock(side_effect=lambda: events.append("end")),
        ), mock.patch.object(
            app_main, "_activate_session", side_effect=lambda *args, **kwargs: events.append("activate")
        ), mock.patch.object(app_main, "_get_current_memory_scope", return_value="session"):
            error = await app_main._auto_resume_session("target", "12345678")

        self.assertIsNone(error)
        self.assertEqual(events, ["end", "activate", "start"])

    async def test_history_get_mismatch_is_read_only_409(self):
        app_main = self.app_main
        persona_manager = mock.Mock()
        persona_manager.active = "current"
        history = mock.Mock()
        history.session_id = "11111111"
        history._session_date = "2026-07-17"
        history._messages = [{"role": "user", "content": "unchanged"}]

        with mock.patch.object(app_main, "persona_manager", persona_manager), mock.patch.object(
            app_main, "history", history
        ):
            response = await app_main.get_history("other", "2026-07-17_12345678")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.body)["error"], "session_mismatch")
        self.assertEqual(history._messages, [{"role": "user", "content": "unchanged"}])

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
        self.assertIn("async def guard_api_requests(request: Request, call_next):", main_source)
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
                {"kind": MEMORY_KIND_SESSION_FACT},
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
        self.assertEqual(kwargs["metadatas"][0]["kind"], MEMORY_KIND_SESSION_FACT)

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

    async def test_store_facts_requires_session_identity(self):
        plugin = MemoryPlugin()
        plugin._collection = mock.MagicMock()
        with self.assertLogs("rp-standalone", level="ERROR"):
            self.assertEqual(
                await plugin._store_facts(["long enough fact"], "persona", ""),
                0,
            )
        plugin._collection.get.assert_not_called()

    async def test_stats_classifies_kinds_and_counts_orphans_without_documents(self):
        collection = mock.MagicMock()
        collection.get.return_value = {
            "ids": ["session-ok", "session-orphan", "persona", "old"],
            "metadatas": [
                {"persona_id": "p", "session_id": "s1", "kind": MEMORY_KIND_SESSION_FACT},
                {"persona_id": "p", "session_id": "gone", "kind": MEMORY_KIND_SESSION_FACT},
                {"persona_id": "p", "kind": MEMORY_KIND_PERSONA_BASE},
                {"persona_id": "p"},
            ],
        }
        plugin = MemoryPlugin()
        plugin._collection = collection

        stats = await plugin.stats({("p", "s1")})

        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["by_kind"], {
            MEMORY_KIND_SESSION_FACT: 2,
            MEMORY_KIND_PERSONA_BASE: 1,
            MEMORY_KIND_LEGACY: 1,
        })
        self.assertEqual(stats["orphan_session_facts"], 1)
        collection.get.assert_called_once_with(include=["metadatas"])

    async def test_orphan_preview_and_deletes_use_metadata_only(self):
        collection = mock.MagicMock()
        collection.get.side_effect = [
            {
                "ids": ["ok", "orphan"],
                "metadatas": [
                    {"persona_id": "p", "session_id": "s1", "kind": MEMORY_KIND_SESSION_FACT},
                    {"persona_id": "p", "session_id": "gone", "kind": MEMORY_KIND_SESSION_FACT},
                ],
            },
            {"ids": ["orphan"], "metadatas": [{}]},
            {"ids": ["base", "fact"], "metadatas": [{}, {}]},
        ]
        plugin = MemoryPlugin()
        plugin._collection = collection

        preview = await plugin.preview_orphans({("p", "s1")})
        deleted_session = await plugin.delete_session("p", "gone")
        deleted_persona = await plugin.delete_persona("p")

        self.assertEqual(preview, [{"id": "orphan", "persona_id": "p", "session_id": "gone"}])
        self.assertEqual(deleted_session, 1)
        self.assertEqual(deleted_persona, 2)
        self.assertEqual(collection.delete.call_args_list, [
            mock.call(ids=["orphan"]),
            mock.call(ids=["base", "fact"]),
        ])
        for call in collection.get.call_args_list:
            self.assertEqual(call.kwargs["include"], ["metadatas"])

    async def test_record_preview_is_metadata_only_and_marks_orphans(self):
        collection = mock.MagicMock()
        collection.get.return_value = {
            "ids": ["fact", "base", "old"],
            "metadatas": [
                {"persona_id": "p", "session_id": "gone", "kind": MEMORY_KIND_SESSION_FACT},
                {"persona_id": "p", "kind": MEMORY_KIND_PERSONA_BASE, "source": "SOUL.md"},
                {"persona_id": "p", "source": "legacy-source"},
            ],
        }
        plugin = MemoryPlugin()
        plugin._collection = collection

        records = await plugin.preview_records(set())

        self.assertEqual(records, [
            {"id": "fact", "kind": MEMORY_KIND_SESSION_FACT, "persona_id": "p", "session_id": "gone", "source": "", "orphan": True},
            {"id": "base", "kind": MEMORY_KIND_PERSONA_BASE, "persona_id": "p", "session_id": "", "source": "SOUL.md", "orphan": False},
            {"id": "old", "kind": MEMORY_KIND_LEGACY, "persona_id": "p", "session_id": "", "source": "legacy-source", "orphan": False},
        ])
        collection.get.assert_called_once_with(include=["metadatas"])

    async def test_management_deletes_are_idempotent_and_metadata_only(self):
        collection = mock.MagicMock()
        collection.get.side_effect = [
            {"ids": ["a", "b"], "metadatas": [{}, {}]},
            {"ids": ["a", "b"], "metadatas": [{}, {}]},
            {
                "ids": ["valid", "orphan"],
                "metadatas": [
                    {"persona_id": "p", "session_id": "s1", "kind": MEMORY_KIND_SESSION_FACT},
                    {"persona_id": "p", "session_id": "gone", "kind": MEMORY_KIND_SESSION_FACT},
                ],
            },
        ]
        plugin = MemoryPlugin()
        plugin._collection = collection

        self.assertEqual(await plugin.delete_all(), 2)
        self.assertEqual(await plugin.delete_records(["missing"]), 0)
        self.assertEqual(await plugin.delete_orphans({("p", "s1")}), 1)
        self.assertEqual(collection.delete.call_args_list, [
            mock.call(ids=["a", "b"]),
            mock.call(ids=["orphan"]),
        ])
        for call in collection.get.call_args_list:
            self.assertEqual(call.kwargs["include"], ["metadatas"])

    async def test_memory_queries_only_session_facts(self):
        collection = mock.MagicMock()
        collection.query.return_value = {"documents": [[]]}
        embedding = mock.MagicMock()
        embedding.encode_query.return_value = [0.1, 0.2]
        plugin = MemoryPlugin()
        plugin._collection = collection
        plugin._embedding_provider = embedding
        ctx = mock.MagicMock()
        ctx.user_input = "long enough query"
        ctx.persona_id = "p"
        ctx.history.session_id = "s1"

        ctx.memory_scope = "session"
        await plugin._on_build_context([], ctx)
        self.assertEqual(collection.query.call_args.kwargs["where"], {"$and": [
            {"persona_id": "p"},
            {"session_id": "s1"},
            {"kind": MEMORY_KIND_SESSION_FACT},
        ]})

        ctx.memory_scope = "persona"
        await plugin._on_build_context([], ctx)
        self.assertEqual(collection.query.call_args.kwargs["where"], {"$and": [
            {"persona_id": "p"},
            {"kind": MEMORY_KIND_SESSION_FACT},
        ]})

    def test_memory_management_endpoints_keep_documents_private(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/memory/stats")', source)
        self.assertIn("from fastapi import FastAPI, HTTPException, Request", source)
        self.assertIn('@app.get("/api/memory/orphans")', source)
        self.assertIn('@app.get("/api/memory/records")', source)
        self.assertIn('@app.post("/api/memory/delete")', source)
        records_start = source.index("async def memory_records")
        records_end = source.index("\ndef _validate_memory_delete", records_start)
        self.assertNotIn("documents", source[records_start:records_end])
        delete_start = source.index("async def memory_delete")
        delete_end = source.index("\ndef _delete_file_resource", delete_start)
        self.assertIn("async with _api_lock:", source[delete_start:delete_end])
        self.assertIn("plugin.delete_orphans(_valid_memory_sessions())", source[delete_start:delete_end])

    def test_memory_delete_scope_parameters_are_strict(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in {"MemoryDeleteRequest", "_validate_memory_delete"}
        ]
        from fastapi import HTTPException
        from pydantic import BaseModel, ConfigDict
        namespace = {
            "BaseModel": BaseModel,
            "ConfigDict": ConfigDict,
            "HTTPException": HTTPException,
            "re": re,
            "validate_persona_id": lambda value: None,
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "memory_delete", "exec"), namespace)
        request_type = namespace["MemoryDeleteRequest"]
        validate = namespace["_validate_memory_delete"]

        valid = [
            {"scope": "all"},
            {"scope": "persona", "persona_id": "p"},
            {"scope": "session", "persona_id": "p", "session_id": "12345678"},
            {"scope": "records", "ids": ["a"]},
            {"scope": "orphans"},
        ]
        for payload in valid:
            validate(request_type(**payload))

        invalid = [
            {"scope": "all", "persona_id": None},
            {"scope": "persona"},
            {"scope": "persona", "persona_id": "p", "ids": ["a"]},
            {"scope": "session", "persona_id": "p", "session_id": "bad"},
            {"scope": "records", "ids": []},
            {"scope": "records", "ids": ["a", "a"]},
            {"scope": "records", "ids": [" "]},
            {"scope": "records", "ids": [str(i) for i in range(501)]},
            {"scope": "orphans", "session_id": None},
        ]
        for payload in invalid:
            with self.assertRaises(HTTPException):
                validate(request_type(**payload))

    async def test_persona_base_index_is_deterministic_and_replaces_old_records(self):
        collection = mock.MagicMock()
        collection.get.return_value = {"ids": ["old"], "metadatas": [{}]}
        embedding = mock.MagicMock()
        embedding.encode.return_value = [[0.1], [0.2], [0.3]]
        plugin = MemoryPlugin()
        plugin._collection = collection
        plugin._embedding_provider = embedding

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            persona_dir = Path(tmp)
            (persona_dir / "SOUL.md").write_text("# Soul\nName: Alice\n", encoding="utf-8")
            (persona_dir / "SKILL.md").write_text("# Skill\n- Talk", encoding="utf-8")
            (persona_dir / "style.yaml").write_text("tone: calm\n", encoding="utf-8")
            first = await plugin.index_persona_base("alice", persona_dir)
            second = await plugin.index_persona_base("alice", persona_dir)

        self.assertEqual(first, second)
        self.assertEqual(first["indexed_count"], 3)
        embedding.encode.assert_called_with([
            "[SOUL.md]\n# Soul\nName: Alice",
            "[SKILL.md]\n# Skill\n- Talk",
            "[style.yaml]\ntone: calm",
        ])
        self.assertEqual(collection.delete.call_args_list, [
            mock.call(ids=["old"]),
            mock.call(ids=["old"]),
        ])
        self.assertEqual(collection.upsert.call_count, 2)
        first_upsert = collection.upsert.call_args_list[0].kwargs
        second_upsert = collection.upsert.call_args_list[1].kwargs
        self.assertEqual(first_upsert["ids"], second_upsert["ids"])
        self.assertEqual(first_upsert["metadatas"], second_upsert["metadatas"])
        for source, document, memory_id, metadata in zip(
            ("SOUL.md", "SKILL.md", "style.yaml"),
            first_upsert["documents"],
            first_upsert["ids"],
            first_upsert["metadatas"],
        ):
            self.assertEqual(memory_id, persona_base_id("alice", source, document))
            self.assertEqual(metadata["kind"], MEMORY_KIND_PERSONA_BASE)
            self.assertEqual(metadata["source_hash"], first["source_hash"])

    async def test_persona_base_requires_complete_persona_before_embedding(self):
        plugin = MemoryPlugin()
        plugin._collection = mock.MagicMock()
        plugin._embedding_provider = mock.MagicMock()
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            (Path(tmp) / "SOUL.md").write_text("Soul", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete_persona"):
                await plugin.index_persona_base("alice", Path(tmp))
        plugin._embedding_provider.encode.assert_not_called()
        plugin._collection.get.assert_not_called()

    async def test_persona_save_succeeds_with_index_warning(self):
        import main as app_main

        studio = mock.MagicMock()
        studio.delete_draft = mock.AsyncMock()
        manager = mock.MagicMock()
        manager.has.return_value = True
        manager.get.return_value = studio
        warning = {"code": "index_failed", "rebuild_available": True}
        with mock.patch.object(app_main, "plugin_manager", manager), mock.patch.object(
            app_main, "_index_persona_base", mock.AsyncMock(return_value=warning)
        ) as index_mock:
            result = await app_main.save_persona(
                app_main.SavePersonaRequest(persona_id="alice", draft={"soul_md": "Soul"})
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["warning"], {"resource": "persona_base", **warning})
        studio.save.assert_called_once()
        studio.delete_draft.assert_awaited_once_with("alice")
        index_mock.assert_awaited_once_with("alice")

    @staticmethod
    def _write_complete_persona(source_dir: Path):
        source_dir.mkdir(parents=True)
        (source_dir / "SOUL.md").write_text("Soul", encoding="utf-8")
        (source_dir / "SKILL.md").write_text("Skill", encoding="utf-8")
        (source_dir / "style.yaml").write_text(
            """style:
  viewpoint: ai_character
  person: first
  narration: true
""",
            encoding="utf-8",
        )

    async def test_persona_import_requires_complete_valid_files(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            source_dir.mkdir()
            (source_dir / "SOUL.md").write_text("Soul", encoding="utf-8")
            personas = root / "personas"
            with mock.patch.object(app_main, "PERSONAS_DIR", personas), mock.patch.object(
                app_main, "_index_persona_base", mock.AsyncMock()
            ) as index_mock:
                validation = await app_main.validate_files(
                    app_main.ValidateFilesRequest(source_dir=str(source_dir))
                )
                result = await app_main.import_persona(app_main.ImportPersonaRequest(
                    persona_id="alice", source_dir=str(source_dir)
                ))

            payload = json.loads(result.body)
            self.assertEqual(validation["status"], "incomplete")
            self.assertEqual(validation["missing"], ["SKILL.md", "style.yaml"])
            self.assertEqual(result.status_code, 422)
            self.assertEqual(payload["error"], "incomplete_persona")
            self.assertFalse((personas / "alice").exists())
            self.assertFalse(personas.exists())
            index_mock.assert_not_awaited()

    async def test_persona_import_rejects_invalid_style(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            self._write_complete_persona(source_dir)
            (source_dir / "style.yaml").write_text(
                """style:
  viewpoint: invalid
  person: first
  narration: true
""",
                encoding="utf-8",
            )
            validation = await app_main.validate_files(
                app_main.ValidateFilesRequest(source_dir=str(source_dir))
            )
            result = await app_main.import_persona(app_main.ImportPersonaRequest(
                persona_id="alice", source_dir=str(source_dir)
            ))

        payload = json.loads(result.body)
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(validation["invalid"], ["style.yaml"])
        self.assertEqual(result.status_code, 422)
        self.assertEqual(payload["error"], "invalid_persona_file")

    async def test_persona_import_is_complete_and_preserves_index_warning(self):
        import main as app_main

        warning = {"code": "memory_unavailable", "rebuild_available": True}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            self._write_complete_persona(source_dir)
            personas = root / "personas"
            with mock.patch.object(app_main, "PERSONAS_DIR", personas), mock.patch.object(
                app_main, "_index_persona_base", mock.AsyncMock(return_value=warning)
            ) as index_mock:
                result = await app_main.import_persona(app_main.ImportPersonaRequest(
                    persona_id="alice", source_dir=str(source_dir)
                ))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["completion"], "complete")
            self.assertEqual(result["imported"], ["SOUL.md", "SKILL.md", "style.yaml"])
            self.assertEqual(result["warning"], {"resource": "persona_base", **warning})
            for filename in result["imported"]:
                self.assertEqual(
                    (personas / "alice" / filename).read_bytes(),
                    (source_dir / filename).read_bytes(),
                )
            index_mock.assert_awaited_once_with("alice")

    async def test_persona_import_collision_preserves_existing_persona(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            self._write_complete_persona(source_dir)
            personas = root / "personas"
            existing = personas / "alice"
            existing.mkdir(parents=True)
            sentinel = existing / "SOUL.md"
            sentinel.write_text("existing", encoding="utf-8")
            with mock.patch.object(app_main, "PERSONAS_DIR", personas):
                result = await app_main.import_persona(app_main.ImportPersonaRequest(
                    persona_id="alice", source_dir=str(source_dir)
                ))

            self.assertEqual(result.status_code, 409)
            self.assertEqual(json.loads(result.body)["error"], "persona_exists")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing")

    async def test_persona_import_copy_failure_leaves_no_temp_directory(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            self._write_complete_persona(source_dir)
            personas = root / "personas"
            with mock.patch.object(app_main, "PERSONAS_DIR", personas), mock.patch(
                "shutil.copy2", side_effect=OSError("copy failed")
            ), mock.patch.object(app_main, "_index_persona_base", mock.AsyncMock()) as index_mock:
                result = await app_main.import_persona(app_main.ImportPersonaRequest(
                    persona_id="alice", source_dir=str(source_dir)
                ))

            self.assertEqual(result.status_code, 500)
            self.assertEqual(json.loads(result.body)["error"], "import_failed")
            self.assertTrue(personas.is_dir())
            self.assertEqual(list(personas.iterdir()), [])
            index_mock.assert_not_awaited()


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

class SessionListContractTests(unittest.TestCase):
    def test_session_list_excludes_state_history_sidecars(self):
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{8}\.jsonl$")
        candidates = [
            "2026-07-17_12345678.jsonl",
            "12345678_state_history.jsonl",
            "2026-07-17_12345678.meta.json",
            "invalid.jsonl",
        ]
        self.assertEqual(
            [name for name in candidates if pattern.fullmatch(name)],
            ["2026-07-17_12345678.jsonl"],
        )

        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            'if re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}_\\d{8}\\.jsonl", f.name)',
            source,
        )


class ChatSendStopTests(unittest.TestCase):
    def test_send_button_is_the_single_send_stop_control(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="send-btn"'), 1)
        self.assertNotIn('id="stop-btn"', html)
        self.assertNotIn('getElementById("stop-btn")', source)
        self.assertIn('streaming ? cancelChat() : send()', source)
        self.assertIn('sendButton.textContent = active ? t("btnStop") : t("sendButton")', source)
        self.assertIn('sendButton.classList.toggle("is-stop", active)', source)

    def test_all_send_paths_share_busy_guard_and_finally_restores_composer(self):
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertIn('if (sending || streaming) return;', source)
        self.assertIn('sending = true;', source)
        self.assertIn('send(newContent);', source)
        self.assertIn('send(text);', source)
        self.assertIn('setComposerStreaming(true);', source)
        self.assertIn('setComposerStreaming(false);', source)
        self.assertIn('if (!controller || cancelling) return;', source)
        self.assertIn('fetch("/api/chat/cancel", { method: "POST" })', source)

    def test_chat_cancel_event_is_not_cleared_after_preprocessing(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        chat_start = source.index('async def chat_sse(req: ChatRequest):')
        stream_start = source.index('async for chunk in chat_stream', chat_start)
        chat_source = source[chat_start:stream_start]
        clear_index = chat_source.index('_cancel_event.clear()')
        user_hook_index = chat_source.index('plugin_manager.dispatch("on_user_message"')
        self.assertLess(clear_index, user_hook_index)
        self.assertEqual(chat_source.count('_cancel_event.clear()'), 1)


class PersonaStudioSaveDisplayTests(unittest.TestCase):
    def test_save_success_keeps_results_and_actions_editable(self):
        source = (ROOT / "frontend" / "js" / "studio.js").read_text(encoding="utf-8")
        start = source.index("async function saveDraft()")
        end = source.index("// ── テスト会話", start)
        save_source = source[start:end]
        self.assertIn('hasDraft = true;', save_source)
        self.assertIn('getElementById("result-panel").style.display = "block"', save_source)
        self.assertIn('getElementById("action-bar").style.display = "flex"', save_source)
        self.assertIn("await loadSavedPersonas();", save_source)
        self.assertNotIn('getElementById("result-panel").style.display = "none"', save_source)
        self.assertNotIn('getElementById("action-bar").style.display = "none"', save_source)

    def test_new_saved_and_form_draft_paths_have_distinct_display_contracts(self):
        source = (ROOT / "frontend" / "js" / "studio.js").read_text(encoding="utf-8")
        show_start = source.index("function showResult(draft)")
        show_end = source.index("function switchResultTab", show_start)
        self.assertIn('getElementById("result-panel").style.display = "block"', source[show_start:show_end])
        load_start = source.index("async function loadDraft(personaId)")
        load_end = source.index("async function deletePersona", load_start)
        self.assertIn("showResult(d);", source[load_start:load_end])
        form_start = source.index("async function loadFormDraft(personaId)")
        form_end = source.index("function setStatus", form_start)
        self.assertIn('getElementById("result-panel").style.display = "none"', source[form_start:form_end])


class PersonaDeleteLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_persona_is_rejected_before_deletion(self):
        import main as app_main

        manager = mock.MagicMock()
        manager.active = "alice"
        with mock.patch.object(app_main, "persona_manager", manager):
            response = await app_main.delete_persona("alice")
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"active_persona", response.body)

    async def test_inactive_persona_deletes_all_resources_and_is_idempotent(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            personas_dir = root / "personas"
            local_sessions = root / "sessions"
            persona_dir = personas_dir / "alice"
            session_dir = local_sessions / "alice"
            log_dir = root / "session-log" / "alice"
            draft_dir = root / "data" / "drafts"
            for path in (base_dir, persona_dir, session_dir, log_dir, draft_dir):
                path.mkdir(parents=True, exist_ok=True)
            (persona_dir / "SOUL.md").write_text("Soul", encoding="utf-8")
            (session_dir / "one.jsonl").write_text("{}", encoding="utf-8")
            (session_dir / "one_state.json").write_text("{}", encoding="utf-8")
            (log_dir / "one.md").write_text("log", encoding="utf-8")
            draft_path = draft_dir / "alice.json"
            draft_path.write_text("{}", encoding="utf-8")
            (base_dir / ".current-session").write_text(
                '{"persona_id":"alice"}', encoding="utf-8"
            )

            studio = mock.MagicMock()
            studio._config = {"data_dir": str(root / "data")}
            async def delete_draft(persona_id):
                if draft_path.exists():
                    draft_path.unlink()
                    return True
                return False
            studio.delete_draft = mock.AsyncMock(side_effect=delete_draft)
            memory = mock.MagicMock()
            memory._collection = object()
            memory.delete_persona = mock.AsyncMock(side_effect=[3, 0])
            plugins = mock.MagicMock()
            plugins.has.side_effect = lambda name: name in {"persona_studio", "memory"}
            plugins.get.side_effect = lambda name: studio if name == "persona_studio" else memory
            manager = mock.MagicMock()
            manager.active = "bob"

            patches = (
                mock.patch.object(app_main, "BASE_DIR", base_dir),
                mock.patch.object(app_main, "PERSONAS_DIR", personas_dir),
                mock.patch.object(app_main, "sessions_dir", local_sessions),
                mock.patch.object(app_main, "plugin_manager", plugins),
                mock.patch.object(app_main, "persona_manager", manager),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                preview = await app_main.preview_delete_persona("alice")
                result = await app_main.delete_persona("alice")
                retry = await app_main.delete_persona("alice")

        self.assertFalse(preview["active"])
        self.assertEqual(preview["resources"], {
            "persona": 1, "sessions": 2, "session_log": 1, "draft": 1,
        })
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deleted_count"], 9)
        self.assertEqual(result["resources"]["memory"]["count"], 3)
        self.assertEqual(retry["status"], "ok")
        self.assertEqual(retry["deleted_count"], 0)
        self.assertEqual(retry["resources"]["persona"]["status"], "not_found")

    async def test_memory_failure_returns_partial_retry_without_exposing_exception(self):
        import main as app_main

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            persona_dir = root / "personas" / "alice"
            base_dir.mkdir()
            persona_dir.mkdir(parents=True)
            (persona_dir / "SOUL.md").write_text("Soul", encoding="utf-8")
            memory = mock.MagicMock()
            memory._collection = object()
            memory.delete_persona = mock.AsyncMock(side_effect=RuntimeError("secret detail"))
            plugins = mock.MagicMock()
            plugins.has.side_effect = lambda name: name == "memory"
            plugins.get.return_value = memory
            manager = mock.MagicMock()
            manager.active = "bob"
            with mock.patch.object(app_main, "BASE_DIR", base_dir), mock.patch.object(
                app_main, "PERSONAS_DIR", root / "personas"
            ), mock.patch.object(app_main, "sessions_dir", root / "sessions"), mock.patch.object(
                app_main, "plugin_manager", plugins
            ), mock.patch.object(app_main, "persona_manager", manager):
                result = await app_main.delete_persona("alice")

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["retry"])
        self.assertEqual(result["failed_resources"], ["memory"])
        self.assertEqual(result["resources"]["memory"]["error"], "delete_failed")
        self.assertNotIn("secret detail", str(result))


class SessionDeleteLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "_delete_file_resource"
        )
        namespace = {"Path": Path, "logger": mock.MagicMock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "delete_resource", "exec"), namespace)
        cls.delete_file_resource = staticmethod(namespace["_delete_file_resource"])
        cls.source = source

    def test_file_resource_delete_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text("test", encoding="utf-8")

            self.assertEqual(
                self.delete_file_resource(path),
                {"status": "deleted", "count": 1},
            )
            self.assertEqual(
                self.delete_file_resource(path),
                {"status": "not_found", "count": 0},
            )

    def test_session_delete_covers_all_resources_and_partial_retry(self):
        for token in (
            "async with _api_lock:",
            '"history": _delete_file_resource',
            '"meta": _delete_file_resource',
            '"state": _delete_file_resource',
            '"state_history": _delete_file_resource',
            '"session_log": _delete_file_resource',
            'memory_plugin.delete_session(persona_id, session_id)',
            '"current_session"',
            '"runtime"',
            '"deleted_count": deleted_count',
            'response["retry"] = True',
        ):
            self.assertIn(token, self.source)

        frontend = (ROOT / "frontend" / "js" / "sessions.js").read_text(encoding="utf-8")
        self.assertIn("data.status === 'partial'", frontend)
        self.assertIn("data.failed_resources", frontend)
        self.assertIn("await loadSessions();", frontend)


class StateHistoryContractTests(unittest.TestCase):
    def test_state_snapshots_follow_history_edits(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('_state_history.jsonl', source)
        self.assertIn('def _record_state_snapshot(state: dict)', source)
        self.assertIn('if count == 0 or count % 2: return', source)
        self.assertIn('if item["message_count"] <= message_count', source)
        self.assertIn('"state_history": _delete_file_resource', source)
        self.assertIn('state = _restore_state_for_history(len(history._messages))', source)

    def test_chat_refreshes_state_after_history_changes(self):
        source = (ROOT / "frontend" / "js" / "chat.js").read_text(encoding="utf-8")
        self.assertIn('async function refreshStatePanel()', source)
        self.assertGreaterEqual(source.count('await refreshStatePanel();'), 4)

class StateReliabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = []
        names = {"_bounded_state", "_merge_state_update", "_StateTrackingStatus"}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
                selected.append(node)
        namespace = {"json": json, "MAX_STATE_LENGTH": 4096}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "state_tracking", "exec"), namespace)
        cls.bounded_state = staticmethod(namespace["_bounded_state"])
        cls.merge_state_update = staticmethod(namespace["_merge_state_update"])
        cls.status_type = namespace["_StateTrackingStatus"]
        cls.source = source

    def test_state_updates_merge_and_only_explicit_resolution_deletes(self):
        previous = {"place": "room", "restraint": "hands", "promise": "kept"}

        merged = self.merge_state_update(previous, {"place": "hall"}, set())
        self.assertEqual(
            merged,
            {"place": "hall", "restraint": "hands", "promise": "kept"},
        )

        resolved = self.merge_state_update(
            merged, {"promise": "renewed"}, {"restraint"}
        )
        self.assertEqual(resolved, {"place": "hall", "promise": "renewed"})

    def test_all_resolved_is_a_valid_empty_state(self):
        merged = self.merge_state_update({"only": "pending"}, {}, {"only"})
        self.assertEqual(merged, {})
        self.assertIn(
            "if state_update_received and not state_overflowed:", self.source
        )
        self.assertIn("_record_state_snapshot(state_dict)", self.source)

    def test_overflow_is_rejected_without_counting_as_missing(self):
        self.assertIsNone(self.bounded_state({"large": "x" * 5000}))
        status = self.status_type()
        status.note_missing_state()
        before = status.missing_count
        status.note_overflow()
        self.assertEqual(status.missing_count, before)
        self.assertTrue(status.consume_overflow_prompt())
        self.assertFalse(status.consume_overflow_prompt())

    def test_tracking_status_resets_at_session_boundaries(self):
        status = self.status_type()
        status.note_missing_state()
        status.note_missing_state()
        status.note_overflow()
        status.reset()
        self.assertEqual(status.missing_count, 0)
        self.assertFalse(status.overflow_prompt_pending)
        self.assertGreaterEqual(self.source.count("_state_tracking.reset()"), 3)
        self.assertIn("def _restore_state_for_history", self.source)

    def test_oversized_seed_is_rejected_before_both_writes(self):
        seed_start = self.source.index("def _seed_initial_state")
        seed_end = self.source.index("\ndef _record_state_snapshot", seed_start)
        seed_source = self.source[seed_start:seed_end]
        bound_pos = seed_source.index("bounded = _bounded_state(state)")
        state_write_pos = seed_source.index("_save_session_state(bounded)")
        snapshot_write_pos = seed_source.index("_save_state_snapshots")
        self.assertLess(bound_pos, state_write_pos)
        self.assertLess(bound_pos, snapshot_write_pos)
        self.assertIn("if bounded is None:", seed_source)
        self.assertIn("return {}", seed_source)

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
        self.assertIn("line.textContent = text", studio)
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

class MemoryManagementUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "frontend" / "settings.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "frontend" / "js" / "settings.js").read_text(encoding="utf-8")

    def test_settings_has_memory_management_tab_and_actions(self):
        for fragment in (
            'data-tab="memory"',
            'id="tab-memory"',
            'id="memory-stat-total"',
            'id="memory-records-body"',
            'id="memory-delete-selected"',
            'id="memory-delete-persona"',
            'id="memory-delete-session"',
            'id="memory-delete-orphans"',
            'id="memory-delete-all"',
        ):
            self.assertIn(fragment, self.html)

    def test_record_rows_use_dom_apis_and_metadata_endpoints(self):
        self.assertIn("memoryFetchJson('/api/memory/stats')", self.js)
        self.assertIn("memoryFetchJson('/api/memory/records')", self.js)
        self.assertIn("document.createElement('tr')", self.js)
        self.assertIn("cell.textContent = value || '—'", self.js)
        self.assertIn("tbody.replaceChildren()", self.js)
        self.assertNotIn("innerHTML", self.js)

    def test_delete_confirms_fresh_count_and_reloads(self):
        start = self.js.index("async function deleteMemoryScope")
        end = self.js.index("/* ── System Prompt", start)
        delete_source = self.js[start:end]
        self.assertIn("const latestStats = await memoryFetchJson('/api/memory/stats')", delete_source)
        self.assertIn("if (!confirm(t('memoryDeleteConfirm', {target, count}))) return", delete_source)
        self.assertIn("memoryFetchJson('/api/memory/delete'", delete_source)
        self.assertIn("await loadMemoryDb()", delete_source)

class ResponsiveLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "frontend" / "css" / "style.css").read_text(encoding="utf-8")

    def test_setup_cards_keep_content_height_and_stack_on_compact_viewports(self):
        self.assertIn("align-content: start;", self.css)
        self.assertIn("grid-auto-rows: max-content;", self.css)
        compact = self.css[self.css.index("@media (max-width: 520px)") :]
        self.assertIn(".persona-grid { grid-template-columns: 1fr; }", compact)

    def test_compact_studio_and_chat_controls_do_not_force_horizontal_overflow(self):
        compact = self.css[self.css.index("@media (max-width: 520px)") :]
        self.assertIn(".studio-field-row { flex-direction: column; }", compact)
        self.assertIn("#input-area textarea { min-width: 0;", compact)
        self.assertIn(".header-left { gap: 6px; overflow: hidden; }", compact)

    def test_state_panel_remains_above_input_and_grows_upward(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('id="state-panel"'), html.index('id="input-area"'))
        compact = self.css[self.css.index("@media (max-width: 520px)") :]
        self.assertIn("#state-panel { padding: 8px 10px; max-height: min(40vh, 240px); }", compact)

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
