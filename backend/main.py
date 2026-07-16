"""
RPスタンドアロンアプリ — FastAPI エントリポイント（コア最小実装）。

起動:
    cd backend
    python main.py                          # 通常（config.yamlのモデル）
    python main.py --debug                  # デバッグログ有効
    python main.py --model MODEL_ID         # モデル上書き（無料モデル等）
    → http://localhost:8765 で起動
    → POST /api/chat の SSE でチャット可能

テスト用簡易HTMLフロント: http://localhost:8765/
"""

import json
import logging
import os
import re
import sys
import time
import traceback
import asyncio
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# .env 自動読込（カレントから親を遡って探索）
def _find_dotenv() -> Path | None:
    """カレントからの相対パスで .env を探す。"""
    d = Path(__file__).resolve().parent
    for _ in range(5):
        p = d / ".env"
        if p.exists():
            return p
        d = d.parent
    return None

_env_path = _find_dotenv()
if _env_path:
    load_dotenv(_env_path)
else:
    load_dotenv()  # python-dotenv のデフォルト探索

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from core.config import (
    load_config,
    update_config_yaml,
    validate_api_settings,
    validate_session_settings,
    validate_style_settings,
    validate_watchdog_settings,
)
from core.api import chat_stream, chat_sync, close_http_client, init_http_client
from core.history import History
from core.persona_manager import PersonaManager, load_style_yaml, validate_persona_id
from core.session_context import SessionContext
from plugins.plugin_manager import PluginManager

# ── ログ設定 ────────────────────────────────────────────────────
DEBUG = "--debug" in sys.argv

# --model 引数で config.yaml のモデルを上書き可能
MODEL_OVERRIDE = None
for i, arg in enumerate(sys.argv):
    if arg == "--model" and i + 1 < len(sys.argv):
        MODEL_OVERRIDE = sys.argv[i + 1]
        break

logger = logging.getLogger("rp-standalone")
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-5s] %(message)s",
    datefmt="%H:%M:%S",
))
logger.addHandler(handler)

# ファイルにも同時出力（バックグラウンドプロセスでも確実に残る）
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    Path(__file__).resolve().parent / "server.log",
    encoding="utf-8",
    maxBytes=1 * 1024 * 1024,  # 1MB
    backupCount=2,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

# uvicorn のアクセスログは INFO 以上のみ表示（DEBUG時以外は抑制気味に）
if not DEBUG:
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logger.info("starting RP Standalone (debug=%s)", DEBUG)

# ── FastAPI アプリ ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # watchdog 事前設定（initialize_all より前に configure が必要）
    if plugin_manager.has("watchdog") and plugin_manager.has("mail"):
        plugin_manager.get("watchdog").set_mail_plugin(
            plugin_manager.get("mail")
        )
        plugin_manager.get("watchdog").configure(
            config.get("watchdog")
        )
        logger.info("watchdog: mail wired + config loaded")

    """起動時: 全プラグイン初期化 + プラグイン間配線。"""
    await plugin_manager.initialize_all()

    # secrets: 機密情報ストア
    if plugin_manager.has("secrets"):
        store_path = BASE_DIR.parent / "data" / "secrets_store.json"
        secrets_plugin = plugin_manager.get("secrets")
        secrets_plugin.configure(str(store_path))
        if plugin_manager.has("persona_studio"):
            plugin_manager.get("persona_studio").set_secret_filter(secrets_plugin.protect_text)
        logger.info("secrets: store=%s", store_path)

    # session_log 出力先
    if plugin_manager.has("session_log"):
        log_dir = BASE_DIR.parent / "session-log"
        plugin_manager.get("session_log").set_log_dir(log_dir)
        logger.info("session_log dir: %s", log_dir)

    # memory: 埋め込みプロバイダ + ChromaDB
    if plugin_manager.has("memory"):
        from core.embedding import SentenceTransformersProvider
        chroma_cfg = config.get("chroma", {})
        embedding = SentenceTransformersProvider(
            chroma_cfg.get("embedding_model", "intfloat/multilingual-e5-small"),
            cache_folder=chroma_cfg.get("embedding_cache"),
        )
        plugin_manager.get("memory").configure(
            embedding_provider=embedding,
            chroma_path=chroma_cfg.get("path", "./data/chroma"),
            config=config,
        )
        logger.info("memory: embedding provider + ChromaDB ready")

    init_http_client()
    logger.info("HTTP client ready")
    try:
        yield
        # 現在のセッションを終了（session_log + memory の事実抽出）
        if persona_manager.active:
            try:
                history.save_turn(force=True)
                ctx = SessionContext(
                    persona_id=persona_manager.active,
                    style=persona_manager.get_active_style() or {},
                    history=history,
                )
                await plugin_manager.dispatch("on_session_end", None, ctx)
            except Exception:
                logger.exception("on_session_end dispatch failed during shutdown")
        # shutdown: 全プラグインのリソース解放
        logger.info("shutting down plugins...")
        await plugin_manager.shutdown_all()
        # セッション状態ファイルをクリア（再起動時に旧セッションが残らないように）
        current_path = BASE_DIR / ".current-session"
        if current_path.exists():
            current_path.unlink()
            logger.info("session state cleared")
        logger.info("shutdown complete")
    finally:
        await close_http_client()
        logger.info("HTTP client closed")


app = FastAPI(title="RP Standalone", lifespan=lifespan)

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "report-uri /api/csp-report"
)
_CSP_REPORT_LIMIT = 500
_csp_report_seen: set[tuple[str, str, str, int]] = set()


@app.middleware("http")
async def add_csp_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP_POLICY
    return response


def _csp_safe_uri(value: object) -> str:
    """CSPレポートのURIからquery/fragmentを除き、ログ長を制限する。"""
    raw = str(value or "")
    if raw in {"inline", "eval"}:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:512]
    return parsed.path[:512]


@app.post("/api/csp-report", status_code=204)
async def receive_csp_report(request: Request):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 16_384:
        return Response(status_code=413)
    body = await request.body()
    if len(body) > 16_384:
        return Response(status_code=413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(status_code=204)
    if not isinstance(payload, dict):
        return Response(status_code=204)
    report = payload.get("csp-report", payload)
    if not isinstance(report, dict):
        return Response(status_code=204)

    directive = str(report.get("effective-directive") or report.get("violated-directive") or "")[:128]
    blocked = _csp_safe_uri(report.get("blocked-uri"))
    source = _csp_safe_uri(report.get("source-file"))
    try:
        line = int(report.get("line-number") or 0)
    except (TypeError, ValueError):
        line = 0
    key = (directive, blocked, source, line)
    if key not in _csp_report_seen and len(_csp_report_seen) < _CSP_REPORT_LIMIT:
        _csp_report_seen.add(key)
        logger.warning(
            "CSP violation: directive=%s blocked=%s source=%s line=%d",
            directive,
            blocked,
            source,
            line,
        )
    return Response(status_code=204)

# ── 設定読込 ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

# ── 静的ファイル配信（フロントエンドSPA） ──────────────────────
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if not FRONTEND_DIR.exists():
    logger.warning("frontend/ directory not found at %s - static files unavailable", FRONTEND_DIR)
app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
config = load_config(CONFIG_PATH)

if MODEL_OVERRIDE:
    config["active_model"] = MODEL_OVERRIDE
    logger.info("model override: %s", MODEL_OVERRIDE)

logger.info("model: %s", config["active_model"])

# ── 起動時バリデーション ──────────────────────────────────────────
if not _env_path:
    logger.warning(".env file not found - API keys may be missing")

provider_id = config.get("active_provider", "")
provider_cfg = config.get("providers", {}).get(provider_id, {})
api_key = provider_cfg.get("api_key", "")
if not api_key:
    logger.error(
        "API key is empty for provider '%s'. "
        "Set the environment variable referenced in config.yaml or create .env.",
        provider_id,
    )

# ── ペルソナ管理 ────────────────────────────────────────────────
PERSONAS_DIR = BASE_DIR / config["personas_dir"]
persona_manager = PersonaManager(
    personas_dir=PERSONAS_DIR,
    default_persona=config["default_persona"],
    default_style=config.get("style", {}),
)
persona_manager.ensure_active()

# ── セッション履歴 ──────────────────────────────────────────────
sessions_dir = BASE_DIR.parent / "sessions"
history = History(
    sessions_dir=sessions_dir,
    persona_id=persona_manager.active,
    max_tokens=config["session"]["max_tokens"],
    save_interval=config["session"]["save_interval"],
)
# システムプロンプト読込（スタイル未ロック時はSOUL.md+SKILL.mdのみ）
system_messages = persona_manager.get_system_prompt()
history.set_system_prompt(system_messages)
# 起動時は履歴を空で開始（セッションは /api/session/start または resume で明示的に開始）
# .current-session が残っている場合は削除（サーバー再起動後は必ずセッション一覧から開始）
current_path = BASE_DIR / ".current-session"
if current_path.exists():
    try:
        current_path.unlink()
        logger.info("startup: cleared stale .current-session")
    except Exception:
        pass

# ── プラグインマネージャ ────────────────────────────────────────
plugin_manager = PluginManager(config["plugins"]["enabled"])

# 同時リクエストによるデータ競合を防止（複数タブ対策）
_api_lock = asyncio.Lock()
_cancel_event = asyncio.Event()  # 生成中断用
class _StateTrackingStatus:
    def __init__(self):
        self.reset()

    def reset(self):
        self.missing_count = 0
        self.overflow_prompt_pending = False

    def note_valid_state(self):
        self.missing_count = 0

    def note_missing_state(self):
        self.missing_count += 1

    def note_overflow(self):
        self.overflow_prompt_pending = True

    def consume_overflow_prompt(self) -> bool:
        pending = self.overflow_prompt_pending
        self.overflow_prompt_pending = False
        return pending


_state_tracking = _StateTrackingStatus()

# persona_studio にAPI設定を注入
if plugin_manager.has("persona_studio"):
    ps = plugin_manager.get("persona_studio")
    ps.configure(config)
    ps.set_cancel_event(_cancel_event)
    logger.info("persona_studio configured")


# ── .last-response タイムスタンプファイル ──────────────────────
LAST_RESPONSE = BASE_DIR.parent / ".last-response"


def touch_last_response():
    """最終応答時刻を更新（watchdog用）。"""
    LAST_RESPONSE.write_text(str(time.time()))


async def _run_with_disconnect_guard(request: Request, coro):
    """切断検知付きで非同期処理を実行。

    - 開始時に _cancel_event をクリア
    - バックグラウンドで切断を監視 → 検知したら _cancel_event.set()
    - 処理中に CancelledError が発生したら "client disconnected" エラーを返す
    """
    _cancel_event.clear()

    async def _watch_disconnect():
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("client disconnected, setting cancel event")
                    _cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        return await coro
    except asyncio.CancelledError:
        logger.info("operation cancelled (client disconnect or user cancel)")
        return {"error": "client disconnected"}
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


def rebuild_system_prompt():
    """スタイル変更後にシステムプロンプトを再構築する。"""
    global system_messages
    system_messages = persona_manager.get_system_prompt()

    # 現在の状態を注入
    state_text = _load_session_state()
    if state_text:
        system_messages.append({"role": "system", "content": state_text})

    # ユーザー設定のグローバルシステムプロンプト（SOUL/Stateの後、制約の前）
    # キャラクター定義より後ろに置くことで、出力形式・文体の最終指示として機能する
    global_prompt = config.get("global_system_prompt", "").strip()
    if global_prompt:
        system_messages.append({"role": "system", "content": global_prompt})

    # ツール呼び出し・コード実行の抑制
    system_messages.append({"role": "system", "content": (
        "【出力制約】\n"
        "あなたはRPキャラクターです。ツール・関数呼び出し・コード実行は一切できません。\n"
        "`````` や ```code``` ブロックを含む出力は禁止です。\n"
        "常に自然言語の会話・描写のみを出力してください。"
    )})

    if _state_tracking.consume_overflow_prompt():
        system_messages.append({"role": "system", "content": "【状態整理】状態が上限を超えたため前回更新は保存していません。解決済み項目を [解決] で明示し、残りを簡潔に統合して有効なSTATEを出力してください。"})
    if _state_tracking.missing_count >= 2:
        system_messages.append({"role": "system", "content": "【状態追跡の再確認】前回STATEが連続して欠落しています。現在の状態を保持し、応答末尾に有効な---STATE---を必ず出力してください。未解決項目は列挙し、すべて解決済みなら各項目を [解決] で明示してください。"})

    # ---STATE--- 出力指示
    system_messages.append({"role": "system", "content": (
        "【状態追跡】\n"
        "応答の最後に、セッション中に記憶すべき全事実をリストアップしてください。\n"
        "変化がなくても毎回出力してください。\n\n"
        "---STATE---\n"
        "- 項目名: 現在の状態の説明\n\n"
        "ルール:\n"
        "- 項目名には誰の状態かを含める（例: 「対象の両手拘束」「葵依の居場所」）\n"
        "- 拘束状態・交わした約束・時間経過で変化する要素・その他忘却すべきでない情報すべてを含める\n"
        "- 値は必要な詳細を自然言語で記述\n"
        "- 前回から変化しない項目は省略しても保持される\n"
        "- 解決した項目だけ値を [解決] として明示する\n"
        "- 【重要】ユーザーの入力が現在の状態と矛盾する・物理的に不可能な場合、\n"
        "  それを受け入れてはいけない。状態に基づいて現実的な反応を返すこと"
    )})

    history.set_system_prompt(system_messages)


def _state_path() -> Path:
    """現在のセッションの状態ファイルパスを返す。"""
    sid = history.session_id or "00000000"
    return BASE_DIR.parent / "sessions" / persona_manager.active / f"{sid}_state.json"


def _state_path_for(persona_id: str, session_id: str) -> Path:
    """指定されたセッションの状態ファイルパスを返す。"""
    return BASE_DIR.parent / "sessions" / persona_id / f"{session_id}_state.json"


def _state_history_path_for(persona_id: str, session_id: str) -> Path:
    return BASE_DIR.parent / "sessions" / persona_id / f"{session_id}_state_history.jsonl"


def _state_history_path() -> Path:
    return _state_history_path_for(persona_manager.active, history.session_id or "00000000")


def _session_meta_path(persona_id: str, session_date: str, session_id: str) -> Path:
    """Return the persistent metadata path for one session."""
    return (
        BASE_DIR.parent / "sessions" / persona_id /
        f"{session_date}_{session_id}.meta.json"
    )


def _load_session_meta(persona_id: str, session_date: str, session_id: str) -> dict:
    """Load session metadata. Older sessions without metadata return an empty dict."""
    path = _session_meta_path(persona_id, session_date, session_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("session metadata load failed: %s", path)
        return {}


def _save_session_meta(state: dict):
    """Persist style and memory scope independently from the active-session pointer."""
    persona_id = state.get("persona_id", "")
    session_date = state.get("session_date", "")
    session_id = state.get("session_id", "")
    if not persona_id or not session_date or not session_id:
        return
    path = _session_meta_path(persona_id, session_date, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "persona_id": persona_id,
        "session_id": session_id,
        "session_date": session_date,
        "style": state.get("style", {}),
        "memory_scope": state.get("memory_scope", "session"),
        "started_at": state.get("started_at", time.time()),
    }
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)

def _load_session_state() -> str:
    """_state.json から現在の状態を読み取り、プロンプト用テキストで返す。"""
    sp = _state_path()
    if not sp.exists():
        return ""
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not state:
        return ""
    lines = ["## Current State"]
    for key, value in state.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


# 状態トラッキングの最大文字数（肥大化防止）
MAX_STATE_LENGTH = 4096


def _bounded_state(state: dict) -> dict | None:
    if not isinstance(state, dict):
        return {}
    if len(json.dumps(state, ensure_ascii=False, indent=2)) > MAX_STATE_LENGTH:
        return None
    return dict(state)


def _merge_state_update(previous: dict, updates: dict, resolved_keys) -> dict:
    merged = dict(previous) if isinstance(previous, dict) else {}
    for key in resolved_keys:
        merged.pop(key, None)
    merged.update(updates)
    return merged


def _write_json_atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _save_session_state(state: dict):
    bounded = _bounded_state(state)
    if bounded is None:
        raise ValueError("state exceeds maximum length")
    _write_json_atomic(_state_path(), bounded)


def _load_state_snapshots() -> list[dict]:
    path = _state_history_path()
    if not path.exists(): return []
    snapshots, previous = [], 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line); count, state = item.get("message_count"), item.get("state")
            if type(count) is not int or count < previous or (count == previous and count != 0) or count % 2 or not isinstance(state, dict): raise ValueError()
            bounded = _bounded_state(state)
            if bounded is None: raise ValueError()
            snapshots.append({"message_count": count, "state": bounded}); previous = count
    except Exception:
        logger.warning("state history ignored: %s", path.name); return []
    return snapshots


def _save_state_snapshots(snapshots: list[dict]):
    path = _state_history_path()
    if not snapshots: path.unlink(missing_ok=True); return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in snapshots) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def _extract_opening_seed() -> dict:
    soul_path = persona_manager.active_dir / "SOUL.md"
    if not soul_path.exists(): return {}
    match = re.search(r"^#{1,2}\s*開始時の状況\s*$([\s\S]*?)(?=^#{1,2}\s|\Z)", soul_path.read_text(encoding="utf-8"), re.MULTILINE)
    content = match.group(1).strip() if match else ""
    return {"開始時の状況": content} if content else {}


def _seed_initial_state() -> dict:
    if _state_path().exists() or _state_history_path().exists(): return {}
    state = _extract_opening_seed()
    bounded = _bounded_state(state)
    if bounded is None:
        logger.warning("initial state seed rejected: exceeds %d chars", MAX_STATE_LENGTH)
        return {}
    if bounded:
        _save_session_state(bounded)
        _save_state_snapshots([{"message_count": 0, "state": bounded, "source": "seed"}])
    return bounded


def _record_state_snapshot(state: dict):
    count = len(history._messages)
    if count == 0 or count % 2: return
    snapshots = [item for item in _load_state_snapshots() if item["message_count"] < count]
    snapshots.append({"message_count": count, "state": _bounded_state(state)})
    _save_state_snapshots(snapshots)


def _restore_state_for_history(message_count: int, preserve_legacy: bool = False) -> dict:
    _state_tracking.reset()
    path = _state_history_path()
    if not path.exists() and preserve_legacy: return {}
    snapshots = [item for item in _load_state_snapshots() if item["message_count"] <= message_count]
    _save_state_snapshots(snapshots)
    state = snapshots[-1]["state"] if snapshots else {}
    if state: _save_session_state(state)
    else: _state_path().unlink(missing_ok=True)
    return state

def _diff_state(old: dict, new: dict) -> dict:
    """新旧状態を比較し、ステータス付きの辞書を返す。

    Returns:
        {key: {"value": str, "status": "new"|"changed"|"unchanged"|"deleted"}}
    """
    result = {}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        in_old = key in old
        in_new = key in new
        if in_new and not in_old:
            result[key] = {"value": new[key], "status": "new"}
        elif in_old and not in_new:
            result[key] = {"value": old[key], "status": "deleted"}
        elif old[key] != new[key]:
            result[key] = {"value": new[key], "status": "changed"}
        else:
            result[key] = {"value": new[key], "status": "unchanged"}
    return result


def _get_current_memory_scope() -> str:
    """.current-session から memory_scope を読み取る。未設定時は "session"。"""
    current_path = BASE_DIR / ".current-session"
    if current_path.exists():
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
            scope = current.get("memory_scope", "session")
            if scope in ("session", "persona"):
                return scope
        except Exception:
            pass
    return "session"


async def _dispatch_session_end_for_active():
    """現在アクティブなペルソナのセッション終了フックを発火。"""
    if persona_manager.active:
        try:
            history.save_turn(force=True)
            old_ctx = SessionContext(
                persona_id=persona_manager.active,
                style=persona_manager.get_active_style() or {},
                history=history,
            )
            await plugin_manager.dispatch("on_session_end", None, old_ctx)
        except Exception:
            logger.exception("on_session_end dispatch failed")


def _activate_session(persona_id: str, session_id: str,
                      jsonl_path: Path | None = None,
                      session_date: str = "",
                      memory_scope: str | None = None,
                      style_override: dict | None = None):
    """ペルソナ切替 + 履歴ロード + スタイルロック + システムプロンプト再構築 + 状態保存。"""
    persona_manager.switch(persona_id)
    history.reload(persona_id)

    if jsonl_path and jsonl_path.exists():
        history._load_specific(jsonl_path)
    else:
        history._messages = []
        history._turn_count = 0

    resolved_date = session_date or time.strftime("%Y-%m-%d")
    history.set_session_id(session_id, date=resolved_date)
    meta = _load_session_meta(persona_id, resolved_date, session_id)
    if style_override is None:
        saved_style = meta.get("style")
        style_override = saved_style if isinstance(saved_style, dict) else None
    if memory_scope not in ("session", "persona"):
        saved_scope = meta.get("memory_scope", "session")
        memory_scope = saved_scope if saved_scope in ("session", "persona") else "session"

    try:
        persona_manager.start_session(style_override)
    except ValueError:
        persona_manager.start_session(
            {"viewpoint": "ai_character", "person": "first", "narration": True}
        )

    _restore_state_for_history(len(history._messages), preserve_legacy=True)
    rebuild_system_prompt()

    session_state = {
        "persona_id": persona_id,
        "session_id": history.session_id,
        "session_date": resolved_date,
        "style": persona_manager.get_active_style(),
        "memory_scope": memory_scope,
        "started_at": meta.get("started_at", time.time()),
    }
    _save_session_meta(session_state)
    (BASE_DIR / ".current-session").write_text(
        json.dumps(session_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def generate_escalation_texts(config: dict) -> list[dict]:
    """現在の会話文脈からエスカレーション通知文面を動的生成する。

    Returns:
        [{"after": 300, "subject": "…", "body": "…"}, ...]
        失敗時は空リスト。
    """
    wd_cfg = config.get("watchdog", {})
    raw_levels = wd_cfg.get("levels", [])
    if not raw_levels:
        return []

    thresholds = [lv["after"] for lv in raw_levels]
    threshold_labels = []
    for t in thresholds:
        if t < 60:
            threshold_labels.append(f"{t}秒")
        else:
            threshold_labels.append(f"{t // 60}分")

    # 直近の会話（最大10往復）
    recent = history.get_context()
    # systemプロンプトを除き、直近のユーザー・アシスタント発言だけ使う
    dialog_lines = []
    for m in recent:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            label = "ユーザー" if role == "user" else "アシスタント"
            dialog_lines.append(f"[{label}] {content}")

    dialog_text = "\n\n".join(dialog_lines[-20:])  # 最大20件
    if not dialog_text:
        dialog_text = "（会話履歴なし）"

    prompt = f"""あなたはRPキャラクターです。以下の会話文脈に基づき、
ユーザーが無反応になった場合のエスカレーション通知文面を3段階で生成してください。

条件：
- 全て日本語、各2〜4文の短さ
- キャラクターの口調・人格を反映
- 件名は2〜4文字の簡潔なもの
- ユーザーキャラが気絶/昏睡/無反応である前提

以下のJSON配列のみを返してください（説明不要）：
[
  {{"after": {thresholds[0]}, "subject": "...", "body": "..."}},
  {{"after": {thresholds[1]}, "subject": "...", "body": "..."}},
  {{"after": {thresholds[2]}, "subject": "...", "body": "..."}}
]

Lv1（{threshold_labels[0]}後）: 穏やかな声かけ
Lv2（{threshold_labels[1]}後）: やや強い警告
Lv3（{threshold_labels[2]}後）: 最終通告

---会話文脈---
{dialog_text}"""

    try:
        raw = await chat_sync(
            [{"role": "user", "content": prompt}],
            config,
        )
        # JSON部分を抽出（コードブロックや前後の説明を除去）
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            logger.warning("escalation gen: no JSON found in response")
            return []
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, list) or len(parsed) != 3:
            logger.warning("escalation gen: unexpected format")
            return []
        logger.info(
            "escalation texts generated: %s / %s / %s",
            parsed[0].get("subject", "?"),
            parsed[1].get("subject", "?"),
            parsed[2].get("subject", "?"),
        )
        return parsed
    except Exception as e:
        logger.error("escalation gen failed: %s", e)
        return []


# ── Pydantic リクエストモデル ────────────────────────────────────


class ProviderRequest(BaseModel):
    provider: str
    model: str = ""
    models: list[str] | None = None


class ApiParamsRequest(BaseModel):
    api: dict = Field(default_factory=dict)


class WatchdogRequest(BaseModel):
    watchdog: dict = Field(default_factory=dict)


class SessionConfigRequest(BaseModel):
    session: dict = Field(default_factory=dict)


class StyleRequest(BaseModel):
    style: dict = Field(default_factory=dict)


class ExtractionConfigRequest(BaseModel):
    fallback_chain: list[dict] = Field(default_factory=list)


class SystemPromptRequest(BaseModel):
    system_prompt: str = ""


class StartSessionRequest(BaseModel):
    persona_id: str = ""
    style_override: dict | None = None
    memory_scope: str = "session"  # "session" | "persona"


class ResumeSessionRequest(BaseModel):
    session_id: str  # "persona_id/YYYY-MM-DD_HHMMSSRR"


class UpdateMessageRequest(BaseModel):
    index: int
    content: str
    persona_id: str = ""
    session_id: str = ""


class DeleteMessageRequest(BaseModel):
    index: int
    persona_id: str = ""
    session_id: str = ""


class TruncateRequest(BaseModel):
    from_index: int
    persona_id: str = ""
    session_id: str = ""


class EstimateStyleRequest(BaseModel):
    soul_md_text: str


class ExtractFieldsRequest(BaseModel):
    text: str


class SaveDraftRequest(BaseModel):
    persona_id: str
    data: dict  # フォーム全状態


class LoadDraftRequest(BaseModel):
    persona_id: str


class ConvertFreetextRequest(BaseModel):
    text: str
    style_override: dict | None = None


class RefineRequest(BaseModel):
    draft: dict = Field(default_factory=dict)
    instruction: str = ""


class TestChatRequest(BaseModel):
    draft: dict = Field(default_factory=dict)
    message: str = ""


class SavePersonaRequest(BaseModel):
    persona_id: str = ""
    draft: dict = Field(default_factory=dict)


class ValidateFilesRequest(BaseModel):
    source_dir: str = ""


class ImportPersonaRequest(BaseModel):
    persona_id: str = ""
    source_dir: str = ""


class SwitchPersonaRequest(BaseModel):
    persona_id: str = ""


class ChatRequest(BaseModel):
    text: str
    persona_id: str = ""
    session_id: str = ""
    resend: bool = False


class SecretRegisterRequest(BaseModel):
    value: str
    label: str = ""


class SecretNormalizeRequest(BaseModel):
    text: str


class SecretRevealRequest(BaseModel):
    placeholder: str


# ── REST API ─────────────────────────────────────────────────────

_secret_reveal_times: deque[float] = deque()


def _secrets_plugin():
    return plugin_manager.get("secrets") if plugin_manager.has("secrets") else None


def _is_registered_secret_reference(reference: str) -> bool:
    plugin = _secrets_plugin()
    return plugin is not None and plugin.get_entry(reference) is not None


plugin_manager.set_secret_validator(_is_registered_secret_reference)


def _protect_secret_data(value):
    """ネストしたStudioデータから既存構文と登録済み実値を除去する。"""
    plugin = _secrets_plugin()
    if plugin is None:
        return value
    if isinstance(value, str):
        return plugin.protect_text(value)
    if isinstance(value, list):
        return [_protect_secret_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _protect_secret_data(item) for key, item in value.items()}
    return value


def _no_store_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    parsed = urlparse(origin)
    return (
        parsed.scheme == request.url.scheme
        and parsed.netloc == request.headers.get("host", "")
    )


@app.get("/api/plugins/ui")
async def get_plugin_ui():
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={
            "version": 10,
            "plugins": plugin_manager.collect_ui_definitions(),
        },
        headers=_no_store_headers(),
    )


@app.post("/api/plugins/{plugin_name}/actions/{action}")
async def run_plugin_ui_action(plugin_name: str, action: str, request: Request):
    from fastapi.responses import JSONResponse

    if not _same_origin(request):
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "forbidden", "data": {}},
            headers=_no_store_headers(),
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0 or parsed_length > 16_384:
                raise ValueError
        except ValueError:
            return JSONResponse(
                status_code=413,
                content={"status": "error", "message": "payload too large", "data": {}},
                headers=_no_store_headers(),
            )

    body = await request.body()
    if len(body) > 16_384:
        return JSONResponse(
            status_code=413,
            content={"status": "error", "message": "payload too large", "data": {}},
            headers=_no_store_headers(),
        )
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "invalid payload", "data": {}},
            headers=_no_store_headers(),
        )

    ctx = SessionContext(
        persona_id=persona_manager.active or "",
        style=persona_manager.get_active_style() or {},
        history=history,
    )
    try:
        result = await plugin_manager.dispatch_ui_action(
            plugin_name,
            action,
            payload,
            ctx,
        )
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "plugin action not found", "data": {}},
            headers=_no_store_headers(),
        )
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "invalid payload", "data": {}},
            headers=_no_store_headers(),
        )
    return JSONResponse(
        status_code=200,
        content=result,
        headers=_no_store_headers(),
    )


@app.get("/api/secrets/status")
async def secrets_status():
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={"enabled": _secrets_plugin() is not None},
        headers=_no_store_headers(),
    )


@app.post("/api/secrets/register")
async def register_secret(req: SecretRegisterRequest, request: Request):
    from fastapi.responses import JSONResponse
    plugin = _secrets_plugin()
    value, label = req.value, req.label.strip()
    if not _same_origin(request):
        return JSONResponse(status_code=403, content={"error": "forbidden"}, headers=_no_store_headers())
    if plugin is None:
        return JSONResponse(status_code=404, content={"error": "secrets_unavailable"}, headers=_no_store_headers())
    if not value.strip() or len(value) > 10000 or len(label) > 100:
        return JSONResponse(status_code=422, content={"error": "invalid_secret"}, headers=_no_store_headers())
    placeholder = plugin.register(value, label)
    return JSONResponse(content={"placeholder": placeholder, "label": label}, headers=_no_store_headers())


@app.post("/api/secrets/normalize")
async def normalize_secret_text(req: SecretNormalizeRequest, request: Request):
    from fastapi.responses import JSONResponse
    plugin = _secrets_plugin()
    if not _same_origin(request):
        return JSONResponse(status_code=403, content={"error": "forbidden"}, headers=_no_store_headers())
    if len(req.text) > 100000:
        return JSONResponse(status_code=422, content={"error": "invalid_secret_text"}, headers=_no_store_headers())
    text = plugin.protect_text(req.text) if plugin else req.text
    return JSONResponse(content={"text": text}, headers=_no_store_headers())


@app.post("/api/secrets/reveal")
async def reveal_secret(req: SecretRevealRequest, request: Request):
    from fastapi.responses import JSONResponse
    if not _same_origin(request):
        return JSONResponse(status_code=403, content={"error": "forbidden"}, headers=_no_store_headers())
    now = time.monotonic()
    while _secret_reveal_times and now - _secret_reveal_times[0] > 60:
        _secret_reveal_times.popleft()
    if len(_secret_reveal_times) >= 30:
        return JSONResponse(status_code=429, content={"error": "rate_limited"}, headers=_no_store_headers())
    _secret_reveal_times.append(now)
    plugin = _secrets_plugin()
    entry = plugin.get_entry(req.placeholder.strip()) if plugin else None
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "secret_not_found"}, headers=_no_store_headers())
    return JSONResponse(
        content={"value": entry.get("value", ""), "label": entry.get("label", "")},
        headers=_no_store_headers(),
    )

@app.get("/api/config/model")
async def get_model():
    """現在のモデル名を返す（フロント表示用）。"""
    return {"model": config["active_model"]}


@app.get("/api/config/providers")
async def get_providers():
    """プロバイダ一覧と現在の選択状態を返す。"""
    result = {
        "active_provider": config.get("active_provider", ""),
        "active_model": config.get("active_model", ""),
        "providers": {},
    }
    for pid, pdata in config.get("providers", {}).items():
        result["providers"][pid] = {
            "models": pdata.get("models", []),
            "api_type": pdata.get("api_type", "openai"),
        }
    return result


@app.get("/api/config/full")
async def get_full_config():
    """全設定を返す（設定画面用）。APIキーはマスク。"""
    import copy
    full = copy.deepcopy(config)
    # APIキーはマスク
    for pid, pdata in full.get("providers", {}).items():
        if "api_key" in pdata and pdata["api_key"]:
            pdata["api_key"] = "***"
    # nested dict の上書きを防ぐため providers は shallow copy 済み
    return full


@app.post("/api/config/provider")
async def set_provider(req: ProviderRequest):
    """プロバイダとモデルを切り替える（config.yaml 書き戻し）。
    models 配列が渡された場合は当該プロバイダのモデルリストも更新する。
    model が空の場合は models の先頭を active_model として使用する。
    """
    provider = req.provider
    model = req.model
    models = req.models

    if provider not in config.get("providers", {}):
        return {"error": f"provider '{provider}' not found"}

    if models is not None:
        if not isinstance(models, list) or not all(isinstance(m, str) and m.strip() for m in models):
            return {"error": "models must be a list of non-empty strings"}
        models = [m.strip() for m in models if m.strip()]
        if not models:
            return {"error": "models list is empty"}
    else:
        models = list(config["providers"][provider].get("models", []))

    # model が指定されていない or リストにない → 先頭を使う
    if not model or model not in models:
        model = models[0]

    def mutator(raw: dict):
        raw["active_provider"] = provider
        raw["active_model"] = model
        raw["providers"][provider]["models"] = models

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用

    logger.info("config updated: provider=%s model=%s models=%d", provider, model, len(models))
    return {"status": "ok", "active_provider": provider, "active_model": model, "models": models}


@app.post("/api/config/api")
async def set_api_params(req: ApiParamsRequest):
    """API 共通パラメータを更新。"""
    try:
        update = validate_api_settings(req.api)
    except ValueError as e:
        return {"error": str(e)}

    def mutator(raw: dict):
        raw.setdefault("api", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用
    logger.info("api params updated: %s", update)
    return {"status": "ok", "api": config["api"]}


@app.post("/api/config/watchdog")
async def set_watchdog(req: WatchdogRequest):
    """Watchdog 設定を更新。"""
    try:
        watchdog_update = validate_watchdog_settings(req.watchdog)
    except ValueError as e:
        return {"error": str(e)}
    watchdog = dict(config.get("watchdog", {}))
    watchdog.update(watchdog_update)

    def mutator(raw: dict):
        raw["watchdog"] = watchdog

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用

    # 実行中の watchdog プラグインにも反映
    if plugin_manager.has("watchdog"):
        plugin_manager.get("watchdog").configure(watchdog)

    logger.info("watchdog config updated")
    return {"status": "ok", "watchdog": watchdog}


@app.post("/api/config/session")
async def set_session_config(req: SessionConfigRequest):
    """セッション設定を更新。"""
    try:
        update = validate_session_settings(req.session)
    except ValueError as e:
        return {"error": str(e)}

    def mutator(raw: dict):
        raw.setdefault("session", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用
    history.max_tokens = config["session"]["max_tokens"]
    history.save_interval = config["session"]["save_interval"]
    logger.info("session config updated: %s", update)
    return {"status": "ok", "session": config["session"]}


@app.post("/api/config/reset")
async def reset_config():
    """config.yaml をデフォルトにリセット（バックアップしてから config.default.yaml をコピー）。"""
    import shutil

    config_path = CONFIG_PATH
    default_path = BASE_DIR / "config.default.yaml"
    backup_path = config_path.with_suffix(".yaml.bak")
    shutil.copy2(config_path, backup_path)

    if not default_path.exists():
        return {"error": "config.default.yaml not found"}

    shutil.copy2(default_path, config_path)

    # メモリ上の config も再読み込み
    new_config = load_config(config_path)
    config.clear()
    config.update(new_config)

    logger.info("config reset to default (backup: %s)", backup_path)
    return {"status": "ok", "message": "設定をリセットしました。再起動してください。"}


@app.post("/api/config/style")
async def set_style(req: StyleRequest):
    """グローバル文体設定を更新。"""
    try:
        update = validate_style_settings(req.style)
    except ValueError as e:
        return {"error": str(e)}

    def mutator(raw: dict):
        raw.setdefault("style", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用
    persona_manager.default_style = dict(config["style"])
    logger.info("global style updated: %s", update)
    return {"status": "ok", "style": config["style"]}


@app.post("/api/config/extraction")
async def set_extraction_config(req: ExtractionConfigRequest):
    """抽出タスク用フォールバックチェーンを更新。"""
    chain = req.fallback_chain

    # バリデーション
    if len(chain) > 5:
        return {"error": "fallback_chain は最大5件です"}
    valid_providers = config.get("providers", {})
    for entry in chain:
        provider = entry.get("provider", "")
        model = entry.get("model", "")
        if not isinstance(provider, str) or not isinstance(model, str):
            return {"error": "provider と model は文字列で指定してください"}
        provider = provider.strip()
        model = model.strip()
        if not provider or not model:
            return {"error": "各エントリに provider と model が必要です"}
        if provider not in valid_providers:
            return {"error": f"provider '{provider}' not found"}
        if model not in valid_providers[provider].get("models", []):
            return {"error": f"model '{model}' is not configured for provider '{provider}'"}
        entry["provider"] = provider
        entry["model"] = model

    def mutator(raw: dict):
        raw.setdefault("extraction", {})["fallback_chain"] = chain

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用

    logger.info("extraction fallback chain updated: %d entries", len(chain))
    return {"status": "ok", "extraction": config.get("extraction", {})}


@app.post("/api/config/system-prompt")
async def set_system_prompt(req: SystemPromptRequest):
    """ユーザー設定のグローバルシステムプロンプトを更新。"""
    prompt = req.system_prompt

    def mutator(raw: dict):
        raw["global_system_prompt"] = prompt

    update_config_yaml(CONFIG_PATH, mutator)
    mutator(config)  # メモリ上の config にも同じ変更を適用

    char_count = len(prompt)
    rebuild_system_prompt()
    logger.info("global system prompt updated: %d chars", char_count)
    return {
        "status": "ok",
        "global_system_prompt": config.get("global_system_prompt", ""),
        "char_count": char_count,
    }


@app.get("/api/persona/list")
async def list_personas(status: str = ""):
    """ペルソナ一覧を返す。

    Query params:
      status: "" (全件) / "saved" (完成済みのみ) / "draft_only" (下書きのみ)
    """
    personas = persona_manager.list_personas()
    persona_ids = {p["id"] for p in personas}

    # 下書きの有無を付与
    draft_dir = BASE_DIR / "data" / "drafts"
    for p in personas:
        draft_path = draft_dir / f"{p['id']}.json"
        p["has_draft"] = draft_path.exists()
        p["status"] = "saved"

    # 下書きのみ（SOUL/SKILLなし）のエントリを追加
    if draft_dir.exists():
        import json
        for draft_file in draft_dir.glob("*.json"):
            draft_id = draft_file.stem
            if draft_id in persona_ids:
                continue
            try:
                draft_data = json.loads(draft_file.read_text(encoding="utf-8"))
                name = (draft_data.get("fields", {}).get("name", "") or
                        draft_data.get("name", "") or draft_id)
            except Exception:
                name = draft_id
            personas.append({
                "id": draft_id,
                "name": name,
                "has_draft": True,
                "status": "draft_only",
                "updated": "",
            })

    # statusフィルタ
    if status == "saved":
        personas = [p for p in personas if p.get("status") == "saved"]
    elif status == "draft_only":
        personas = [p for p in personas if p.get("status") == "draft_only"]

    return personas


@app.get("/api/sessions/list")
async def list_sessions():
    """全ペルソナのセッション一覧を返す。"""
    sessions = []
    sessions_dir = BASE_DIR.parent / "sessions"
    if not sessions_dir.exists():
        return {"sessions": []}

    personas = persona_manager.list_personas()
    persona_map = {p["id"]: p["name"] for p in personas}

    for persona_dir in sessions_dir.iterdir():
        if not persona_dir.is_dir():
            continue
        persona_id = persona_dir.name
        persona_name = persona_map.get(persona_id, persona_id)

        # 正規の会話JSONLだけを日付降順で取得（state history等のsidecarを除外）
        jsonl_files = sorted(
            (f for f in persona_dir.glob("*.jsonl")
             if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}\.jsonl", f.name)),
            reverse=True,
        )
        for f in jsonl_files:
            stem = f.stem
            # ファイル名は常に YYYY-MM-DD_HHMMSSRR.jsonl 形式
            stem = f.stem
            parts = stem.split("_", 1)
            date_str = parts[0]
            session_id = parts[1]  # HHMMSSRR（必須）
            try:
                # created: HHMMSS から時刻を復元
                hh, mm, ss = session_id[:2], session_id[2:4], session_id[4:6]
                created = f"{date_str}T{hh}:{mm}:{ss}"
                # 更新日時はファイルの mtime
                updated = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime))

                # 往復数をカウント（行数 / 2）
                lines = f.read_text(encoding="utf-8").strip().split("\n")
                turns = len([l for l in lines if l.strip()]) // 2

                display_id = f"{persona_id}/{stem}"
                sessions.append({
                    "id": display_id,
                    "persona_id": persona_id,
                    "persona_name": persona_name,
                    "created": created,
                    "updated": updated,
                    "turns": turns,
                })
            except Exception:
                continue

    # 更新日時降順でソート
    sessions.sort(key=lambda x: x["updated"], reverse=True)

    # 現在進行中のセッションがあれば先頭に追加
    current_path = BASE_DIR / ".current-session"
    if current_path.exists():
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
            pid = current.get("persona_id", "")
            sid = current.get("session_id", "")
            if pid and sid:
                sdate = current.get("session_date", time.strftime("%Y-%m-%d"))
                # 既存リストに同じセッションがあるか確認
                existing_ids = {s["id"] for s in sessions}
                current_id = f"{pid}/{sdate}_{sid}"
                if current_id not in existing_ids:
                    pname = persona_map.get(pid, pid)
                    started = current.get("started_at", time.time())
                    created = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started))
                    sessions.insert(0, {
                        "id": current_id,
                        "persona_id": pid,
                        "persona_name": pname,
                        "created": created,
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                 time.localtime(started)),
                        "turns": 0,
                    })
        except Exception:
            pass

    return {"sessions": sessions}


def _valid_memory_sessions() -> set[tuple[str, str]]:
    """Return (persona_id, session_id) pairs from canonical conversation files."""
    sessions_dir = BASE_DIR.parent / "sessions"
    valid = set()
    if not sessions_dir.exists():
        return valid
    for persona_dir in sessions_dir.iterdir():
        if not persona_dir.is_dir():
            continue
        for path in persona_dir.glob("*.jsonl"):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}\.jsonl", path.name):
                valid.add((persona_dir.name, path.stem.split("_", 1)[1]))
    return valid


def _memory_management_plugin():
    if not plugin_manager.has("memory"):
        return None
    plugin = plugin_manager.get("memory")
    return plugin if getattr(plugin, "_collection", None) is not None else None


@app.get("/api/memory/stats")
async def memory_stats():
    """Return metadata-only Memory counts."""
    from fastapi.responses import JSONResponse
    plugin = _memory_management_plugin()
    if plugin is None:
        return JSONResponse(status_code=503, content={"error": "memory_unavailable"})
    try:
        return await plugin.stats(_valid_memory_sessions())
    except Exception as e:
        logger.error("memory stats failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "memory_stats_failed"})


@app.get("/api/memory/orphans")
async def memory_orphans():
    """Preview orphan session facts without returning document contents."""
    from fastapi.responses import JSONResponse
    plugin = _memory_management_plugin()
    if plugin is None:
        return JSONResponse(status_code=503, content={"error": "memory_unavailable"})
    try:
        orphans = await plugin.preview_orphans(_valid_memory_sessions())
        return {"count": len(orphans), "items": orphans}
    except Exception as e:
        logger.error("memory orphan preview failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "memory_orphans_failed"})


def _delete_file_resource(path: Path) -> dict:
    """Delete one file idempotently and return a stable resource result."""
    try:
        if not path.exists():
            return {"status": "not_found", "count": 0}
        path.unlink()
        return {"status": "deleted", "count": 1}
    except Exception:
        logger.exception("resource delete failed: %s", path.name)
        return {"status": "error", "count": 0, "error": "delete_failed"}


@app.delete("/api/sessions/{persona_id}/{date}")
async def delete_session(persona_id: str, date: str):
    """Delete a session across files, logs, Memory, and active runtime state."""
    validate_persona_id(persona_id)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", date):
        return {"error": "invalid format (use YYYY-MM-DD_HHMMSSRR)"}

    async with _api_lock:
        session_date, session_id = date.split("_", 1)
        persona_dir = BASE_DIR.parent / "sessions" / persona_id
        current_path = BASE_DIR / ".current-session"
        results = {
            "history": _delete_file_resource(persona_dir / f"{date}.jsonl"),
            "meta": _delete_file_resource(
                _session_meta_path(persona_id, session_date, session_id)
            ),
            "state": _delete_file_resource(_state_path_for(persona_id, session_id)),
            "state_history": _delete_file_resource(
                _state_history_path_for(persona_id, session_id)
            ),
            "session_log": _delete_file_resource(
                BASE_DIR.parent / "session-log" / persona_id / f"{date}.md"
            ),
        }

        is_current = False
        if current_path.exists():
            try:
                current = json.loads(current_path.read_text(encoding="utf-8"))
                current_date = current.get("session_date", time.strftime("%Y-%m-%d"))
                is_current = (
                    current.get("persona_id") == persona_id
                    and current.get("session_id") == session_id
                    and current_date == session_date
                )
                results["current_session"] = (
                    _delete_file_resource(current_path)
                    if is_current else {"status": "not_target", "count": 0}
                )
            except Exception:
                logger.exception("current session read failed during delete")
                results["current_session"] = {
                    "status": "error", "count": 0, "error": "read_failed"
                }
        else:
            results["current_session"] = {"status": "not_found", "count": 0}

        memory_plugin = _memory_management_plugin()
        if not plugin_manager.has("memory"):
            results["memory"] = {"status": "disabled", "count": 0}
        elif memory_plugin is None:
            results["memory"] = {
                "status": "error", "count": 0, "error": "memory_unavailable"
            }
        else:
            try:
                deleted = await memory_plugin.delete_session(persona_id, session_id)
                results["memory"] = {
                    "status": "deleted" if deleted else "not_found",
                    "count": deleted,
                }
            except Exception:
                logger.exception("memory delete failed for %s/%s", persona_id, session_id)
                results["memory"] = {
                    "status": "error", "count": 0, "error": "delete_failed"
                }

        runtime_matches = (
            persona_manager.active == persona_id
            and history.session_id == session_id
            and getattr(history, "_session_date", "") == session_date
        )
        if is_current or runtime_matches:
            history._messages = []
            history._turn_count = 0
            history._saved_message_count = 0
            history._session_id = ""
            history._session_date = ""
            _state_tracking.reset()
            results["runtime"] = {"status": "cleared", "count": 0}
        else:
            results["runtime"] = {"status": "not_target", "count": 0}

        for parent in (
            persona_dir,
            BASE_DIR.parent / "session-log" / persona_id,
        ):
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception:
                logger.exception("empty directory cleanup failed: %s", parent.name)

        errors = [
            name for name, result in results.items()
            if result.get("status") == "error"
        ]
        deleted_count = sum(int(result.get("count", 0)) for result in results.values())
        status = "ok" if not errors else ("partial" if deleted_count else "error")
        response = {
            "status": status,
            "persona_id": persona_id,
            "date": date,
            "deleted_count": deleted_count,
            "results": results,
        }
        if errors:
            response["retry"] = True
            response["failed_resources"] = errors
        logger.info(
            "session delete result: %s/%s status=%s deleted=%d",
            persona_id, date, status, deleted_count,
        )
        return response

@app.post("/api/persona/switch")
async def switch_persona(req: SwitchPersonaRequest):
    async with _api_lock:
        persona_id = req.persona_id
        if not persona_id:
            return {"error": "persona_id required"}
        try:
            validate_persona_id(persona_id)
            persona_manager.switch(persona_id)
            history.reload(persona_id)
            _state_tracking.reset()
            rebuild_system_prompt()
            # hook: ペルソナ切替（memory等の連動切替）
            ctx = SessionContext(
                persona_id=persona_id,
                style=persona_manager.get_active_style() or {},
                history=history,
            )
            await plugin_manager.dispatch("on_persona_switch", None, ctx)
            logger.info("persona switched: %s", persona_id)
            return {"active_persona": persona_id}
        except ValueError as e:
            logger.error("persona switch failed: %s", e)
            return {"error": str(e)}


@app.get("/api/persona/{persona_id}/style")
async def get_persona_style(persona_id: str):
    """ペルソナのスタイル情報（デフォルト＋プリセット一覧）を返す。

    style.yaml が存在しない場合は persona_studio で SOUL.md から推定を試みる。
    """
    validate_persona_id(persona_id)
    style_path = PERSONAS_DIR / persona_id / "style.yaml"
    raw = load_style_yaml(style_path)
    if raw is None:
        # style.yaml 不在 → persona_studio で推定
        soul_path = PERSONAS_DIR / persona_id / "SOUL.md"
        if soul_path.exists() and plugin_manager.has("persona_studio"):
            try:
                soul_text = soul_path.read_text(encoding="utf-8")
                estimate = await plugin_manager.get("persona_studio").estimate_style_from_soul(soul_text)
                return {"status": "needs_confirmation", "estimate": estimate}
            except Exception as e:
                logger.error("style estimation failed: %s", e)
        return {"status": "needs_manual_setup"}

    return {
        "status": "ok",
        "default_style": raw.get("style", {}),
        "presets": raw.get("presets", []),
        "is_session_started": persona_manager.get_active_style() is not None,
        "active_style": persona_manager.get_active_style(),
    }


@app.post("/api/session/start")
async def start_session(req: StartSessionRequest):
    async with _api_lock:
        """セッションを開始し、スタイルをロックする。

        Body: {"persona_id": "kyouka-detective", "style_override": {...}}
        persona_id が指定された場合はペルソナを切り替える。
        """
        persona_id = req.persona_id.strip()
        if persona_id:
            # persona_id のバリデーション（パストラバーサル防止、防御的）
            try:
                validate_persona_id(persona_id)
            except ValueError as e:
                return {"error": str(e)}

            try:
                # 同一ペルソナを含め、進行中の前セッションを終了して未保存分を確定する
                if persona_manager.active and history.session_id:
                    await _dispatch_session_end_for_active()
                persona_manager.switch(persona_id)
                # 新規セッション → 履歴を空にする（続きからは resume を使う）
                history.reload(persona_id)
                history._messages = []
                history._turn_count = 0
                logger.info("persona switched: %s (new session)", persona_id)
            except ValueError as e:
                return {"error": str(e)}

        # セッションID生成（一意性確保のためランダム2桁付与）
        import random
        session_id = time.strftime("%H%M%S") + str(random.randint(10, 99))
        session_date = time.strftime("%Y-%m-%d")
        history.set_session_id(session_id, date=session_date)

        # 新規セッション：JSONLファイルを空にする（同名の旧データが残っていた場合に備える）
        history._save_full()

        try:
            style = persona_manager.start_session(req.style_override)
        except ValueError as e:
            return {"error": str(e)}

        _state_tracking.reset()
        _seed_initial_state()
        rebuild_system_prompt()

        # セッション状態をファイルに保存
        memory_scope = req.memory_scope if req.memory_scope in ("session", "persona") else "session"
        session_state = {
            "persona_id": persona_manager.active,
            "session_id": session_id,
            "style": style,
            "session_date": session_date,
            "memory_scope": memory_scope,
            "started_at": time.time(),
        }
        state_path = BASE_DIR / ".current-session"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _save_session_meta(session_state)
        state_path.write_text(json.dumps(session_state, ensure_ascii=False, indent=2),
                              encoding="utf-8")

        # hook: on_session_start
        ctx = SessionContext(
            persona_id=persona_manager.active,
            style=style,
            history=history,
            memory_scope=memory_scope,
        )
        await plugin_manager.dispatch("on_session_start", ctx)

        logger.info(
            "session started | persona=%s style=%s",
            persona_manager.active,
            json.dumps(style, ensure_ascii=False),
        )
        return {"status": "ok", "persona_id": persona_manager.active, "style": style,
                "memory_scope": memory_scope}


@app.get("/api/session/current")
async def get_current_session():
    """現在のセッション状態を返す。未開始時は空。"""
    state_path = BASE_DIR / ".current-session"
    if not state_path.exists():
        return {"status": "no_session"}

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "no_session"}

    persona_id = state.get("persona_id", "")
    session_id = state.get("session_id", "")
    session_date = state.get("session_date", time.strftime("%Y-%m-%d"))
    style = state.get("style", {})

    # session_id がないセッションは不完全 → 無効扱い
    if not session_id:
        return {"status": "no_session"}

    persona_name = ""
    for p in persona_manager.list_personas():
        if p["id"] == persona_id:
            persona_name = p["name"]
            break

    return {
        "status": "ok",
        "persona_id": persona_id,
        "persona_name": persona_name,
        "session_id": session_id,
        "session_date": session_date,
        "session_key": f"{session_date}_{session_id}",
        "style": style,
        "started_at": state.get("started_at", 0),
        "memory_scope": state.get("memory_scope", "session"),
    }


@app.get("/api/session/state")
async def get_session_state():
    """現在のセッション状態を返す。"""
    path = _state_path()
    if not path.exists():
        return {"state": {}}
    try:
        return {"state": json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        logger.warning("state ignored: %s", path.name)
        return {"state": {}}


@app.get("/api/session/history")
async def get_history(persona_id: str = "", session_id: str = ""):
    """現在の履歴メッセージを返す。persona_id指定時は自動復元。"""
    err = await _auto_resume_session(persona_id, session_id)
    if err:
        return {"error": err, "messages": []}
    return {
        "messages": [
            {"role": m["role"], "content": m["content"]}
            for m in history._messages
        ]
    }


async def _auto_resume_session(persona_id: str, session_id: str = "") -> str | None:
    """persona_id がサーバー側の active と異なる場合、自動でセッションを復元する。

    Returns:
        エラーメッセージ（文字列）。成功時は None。
    """
    if not persona_id:
        return None  # 一致または空 → 何もしない
    if persona_id == persona_manager.active and not session_id:
        return None

    # persona_id のバリデーション（パストラバーサル防止、防御的）
    try:
        validate_persona_id(persona_id)
    except ValueError as e:
        return str(e)

    # session_id のバリデーション（パストラバーサル防止）
    expected_date = ""
    expected_sid = ""
    if session_id:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", session_id):
            expected_date, expected_sid = session_id.split("_", 1)
        elif re.fullmatch(r"\d{8}", session_id):
            expected_sid = session_id
        else:
            return f"invalid session_id format: {session_id}"

    current_date = getattr(history, "_session_date", "")
    if (persona_id == persona_manager.active and expected_sid == history.session_id
            and (not expected_date or expected_date == current_date)):
        return None

    logger.info(
        "auto-resume: persona mismatch frontend=%s server=%s",
        persona_id, persona_manager.active,
    )
    try:
        # 前のセッションを終了（session_log + memory の事実抽出）
        await _dispatch_session_end_for_active()

        # 履歴のロードまたは初期化
        if session_id:
            # session_id は "YYYY-MM-DD_HHMMSSRR" 形式
            persona_dir = BASE_DIR.parent / "sessions" / persona_id
            if expected_date:
                jsonl_path = persona_dir / f"{expected_date}_{expected_sid}.jsonl"
            else:
                matches = sorted(persona_dir.glob(f"*_{expected_sid}.jsonl"))
                if len(matches) != 1:
                    return f"session not found or ambiguous: {session_id}"
                jsonl_path = matches[0]
                expected_date = jsonl_path.stem.split("_", 1)[0]
            if not jsonl_path.exists():
                return f"session not found: {persona_id}/{session_id}"
            _activate_session(
                persona_id, expected_sid, jsonl_path,
                session_date=expected_date,
            )
        else:
            jsonl_path = None
            ssid = time.strftime("%H%M%S") + str(__import__("random").randint(10, 99))
            _activate_session(persona_id, ssid, jsonl_path)
        logger.info("auto-resume: success persona=%s session=%s", persona_id, history.session_id)
        return None
    except Exception as e:
        logger.error("auto-resume failed: %s", e)
        return str(e)


@app.post("/api/session/resume")
async def resume_session(req: ResumeSessionRequest):
    async with _api_lock:
        """既存セッションを再開する。

        Body: {"session_id": "kyouka-detective/2026-07-06_HHMMSSRR"}
        """
        raw_id = req.session_id.strip()
        if not raw_id or "/" not in raw_id:
            return {"error": "invalid session_id (format: persona_id/YYYY-MM-DD_HHMMSSRR)"}

        persona_id, file_stem = raw_id.split("/", 1)
        try:
            validate_persona_id(persona_id)
        except ValueError as e:
            return {"error": str(e)}

        # ファイル名は常に YYYY-MM-DD_HHMMSSRR 形式
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", file_stem):
            return {"error": "invalid session_id format (YYYY-MM-DD_HHMMSSRR required)"}
        session_id = file_stem.split("_", 1)[1]
        session_date = file_stem.split("_")[0]

        jsonl_path = BASE_DIR.parent / "sessions" / persona_id / f"{file_stem}.jsonl"
        if not jsonl_path.exists():
            return {"error": f"session not found: {raw_id}"}

        # End the previous session only after the requested session is validated.
        if (persona_manager.active and
                (persona_manager.active != persona_id or history.session_id != session_id
                 or getattr(history, "_session_date", "") != session_date)):
            await _dispatch_session_end_for_active()
        try:
            _activate_session(
                persona_id, session_id, jsonl_path, session_date=session_date)
        except Exception as e:
            return {"error": str(e)}

        if jsonl_path.exists():
            logger.info("resume: loaded history from %s (%d messages)",
                         jsonl_path.name, len(history._messages))
        else:
            logger.info("resume: new session (no existing file)")

        # resumed_from を追記
        current_path = BASE_DIR / ".current-session"
        if current_path.exists():
            try:
                state = json.loads(current_path.read_text(encoding="utf-8"))
                state["resumed_from"] = raw_id
                current_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
            except Exception:
                pass

        # hook: on_session_start
        ctx = SessionContext(
            persona_id=persona_id,
            style=persona_manager.get_active_style() or {},
            history=history,
            memory_scope=_get_current_memory_scope(),
        )
        await plugin_manager.dispatch("on_session_start", ctx)

        logger.info("session resumed | persona=%s date=%s", persona_id, file_stem)
        return {"status": "ok", "persona_id": persona_id,
                "style": persona_manager.get_active_style(),
                "memory_scope": _get_current_memory_scope()}


@app.post("/api/session/update-message")
async def update_message(req: UpdateMessageRequest):
    async with _api_lock:
        """指定インデックスのメッセージ内容を更新する。

        Body: {"index": 0, "content": "新しい内容", "persona_id": "...", "session_id": "..."}
        """
        err = await _auto_resume_session(req.persona_id, req.session_id)
        if err:
            return {"error": err}
        index = req.index
        content = _protect_secret_data(req.content)
        if index < 0 or index >= len(history._messages):
            return {"error": f"invalid index (0-{len(history._messages)-1})"}
        history.update_message(index, content)
        state = {}
        if history._messages[index].get("role") == "assistant":
            state = _restore_state_for_history(index)
        logger.info("message updated: index=%d", index)
        return {"status": "ok", "state": state}


@app.post("/api/session/delete-message")
async def delete_message(req: DeleteMessageRequest):
    async with _api_lock:
        """指定インデックスのメッセージを削除する。

        Body: {"index": 0, "persona_id": "...", "session_id": "..."}
        ユーザーメッセージ削除時は対応するアシスタント応答も削除。
        """
        err = await _auto_resume_session(req.persona_id, req.session_id)
        if err:
            return {"error": err}
        index = req.index
        if index < 0 or index >= len(history._messages):
            return {"error": f"invalid index (0-{len(history._messages)-1})"}

        msg = history._messages[index]
        role = msg.get("role", "")
        deleted = 1

        # ユーザーメッセージ削除 → 直後のアシスタント応答も削除
        if role == "user" and index + 1 < len(history._messages):
            next_msg = history._messages[index + 1]
            if next_msg.get("role") == "assistant":
                del history._messages[index + 1]
                deleted = 2

        del history._messages[index]
        history._save_full()
        state = _restore_state_for_history(len(history._messages))
        logger.info("message deleted: index=%d role=%s deleted=%d", index, role, deleted)
        return {"status": "ok", "deleted": deleted, "state": state}


@app.post("/api/session/truncate")
async def truncate_history(req: TruncateRequest):
    async with _api_lock:
        """指定インデックス以降のメッセージをすべて削除する。

        Body: {"from_index": 3, "persona_id": "...", "session_id": "..."}
        from_index のメッセージ自体も削除対象。
        """
        err = await _auto_resume_session(req.persona_id, req.session_id)
        if err:
            return {"error": err}
        from_index = req.from_index
        if from_index < 0 or from_index >= len(history._messages):
            return {"error": f"invalid from_index (0-{len(history._messages)-1})"}
        deleted = len(history._messages) - from_index
        history._messages = history._messages[:from_index]
        history._turn_count = sum(1 for m in history._messages if m.get("role") == "user")
        history._save_full()
        state = _restore_state_for_history(len(history._messages))
        logger.info("history truncated: from_index=%d deleted=%d", from_index, deleted)
        return {"status": "ok", "deleted": deleted, "state": state}


@app.post("/api/session/opening")
async def generate_opening():
    """SOUL.md の「開始時の状況」を読み取って返す。不在時は簡易フォールバック。"""
    if not persona_manager.active:
        return {"error": "no active persona"}

    soul_path = persona_manager.active_dir / "SOUL.md"
    if not soul_path.exists():
        return {"status": "ok", "opening": None}

    soul_md = soul_path.read_text(encoding="utf-8")
    import re

    # 「開始時の状況」セクションを探す
    m = re.search(r"##\s*開始時の状況[\s\S]*?(?=\n##\s|\n---|$)", soul_md)
    if m:
        scene = m.group(0).split("\n", 1)[1].strip() if "\n" in m.group(0) else ""
        if scene:
            return {"status": "ok", "opening": scene}

    # フォールバック: ペルソナ名から簡易生成
    name_match = re.search(r"^#\s*(?:SOUL:\s*)?(.+?)(?:\n|$)", soul_md)
    char_name = name_match.group(1).strip() if name_match else persona_manager.active
    # 名前から不要な接頭辞を除去（「SOUL: 九条鏡花 — 私立探偵」→「九条鏡花」）
    if "—" in char_name:
        char_name = char_name.split("—")[0].strip()
    opening = f"……{char_name}は、いつもと変わらぬ静けさの中で、次の訪問者を待っている。"

    return {"status": "ok", "opening": opening}


# ── persona_studio API ───────────────────────────────────────────


@app.post("/api/persona-studio/cancel")
async def cancel_studio():
    """現在の抽出・生成を中断する。"""
    _cancel_event.set()
    return {"status": "ok"}


@app.post("/api/persona-studio/estimate-style")
async def estimate_style(req: EstimateStyleRequest, request: Request):
    """SOUL.md テキストから文体を推定する。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    soul_text = req.soul_md_text
    if not soul_text:
        return {"error": "soul_md_text required"}
    try:
        result = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").estimate_style_from_soul(soul_text),
        )
        if isinstance(result, dict) and "error" in result:
            return result
        return {"status": "ok", "estimate": result}
    except Exception as e:
        logger.error("estimate_style failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/create-template")
async def create_template(data: dict, request: Request):
    """フォーム入力から SOUL.md / SKILL.md / style を生成。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        result = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").create_via_template(data),
        )
        if isinstance(result, dict) and "error" in result:
            return result
        return {"status": "ok", "draft": result}
    except Exception as e:
        logger.error("create_template failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/extract-fields")
async def extract_fields(req: ExtractFieldsRequest, request: Request):
    """自由記述テキストから CharacterData フィールドを抽出（v3.3）。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        result = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").extract_fields(req.text),
        )
        if isinstance(result, dict) and "error" in result:
            return result
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("extract_fields failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/save-draft")
async def save_draft(req: SaveDraftRequest):
    """フォーム状態をドラフト保存。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        result = await plugin_manager.get("persona_studio").save_draft(
            req.persona_id, _protect_secret_data(req.data),
        )
        return result
    except Exception as e:
        logger.error("save_draft failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/load-draft")
async def load_draft(req: LoadDraftRequest):
    """保存済みドラフトを読込。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        data = await plugin_manager.get("persona_studio").load_draft(
            req.persona_id,
        )
        if data is None:
            return {"status": "not_found"}
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error("load_draft failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/delete-draft")
async def delete_draft(req: LoadDraftRequest):
    """ドラフトを削除。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        deleted = await plugin_manager.get("persona_studio").delete_draft(
            req.persona_id,
        )
        return {"status": "ok", "deleted": deleted}
    except Exception as e:
        logger.error("delete_draft failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/convert-freetext")
async def convert_freetext(req: ConvertFreetextRequest, request: Request):
    """自由記述テキストをペルソナ形式に変換。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        draft = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").convert_freetext(
                req.text, req.style_override,
            ),
        )
        if isinstance(draft, dict) and "error" in draft:
            return draft
        return {"status": "ok", "draft": draft}
    except Exception as e:
        logger.error("convert_freetext failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/refine")
async def refine_draft(req: RefineRequest, request: Request):
    """ドラフトを指示に従って部分修正。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        revised = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").refine(
                req.draft, req.instruction,
            ),
        )
        if isinstance(revised, dict) and "error" in revised:
            return revised
        return {"status": "ok", "draft": revised}
    except Exception as e:
        logger.error("refine failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/test-chat")
async def test_chat(req: TestChatRequest, request: Request):
    """ドラフトのペルソナでテスト会話。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        response_text = await _run_with_disconnect_guard(
            request,
            plugin_manager.get("persona_studio").test_chat(
                req.draft, req.message,
            ),
        )
        if isinstance(response_text, dict) and "error" in response_text:
            return response_text
        return {"status": "ok", "response": response_text}
    except Exception as e:
        logger.error("test_chat failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/save")
async def save_persona(req: SavePersonaRequest):
    """ドラフトを personas/{persona_id}/ に保存。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    persona_id = req.persona_id.strip()
    if not persona_id:
        return {"error": "persona_id required"}
    try:
        validate_persona_id(persona_id)
        plugin_manager.get("persona_studio").save(
            PERSONAS_DIR, persona_id, _protect_secret_data(req.draft)
        )
        # 下書きを自動削除（保存完了後は不要）
        await plugin_manager.get("persona_studio").delete_draft(persona_id)
        logger.info("persona saved: %s", persona_id)
        return {"status": "ok", "persona_id": persona_id}
    except Exception as e:
        logger.error("save failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/validate-files")
async def validate_files(req: ValidateFilesRequest):
    """指定フォルダ内のペルソナファイルの有無を確認する。"""
    source_dir = req.source_dir.strip()
    if not source_dir:
        return {"error": "source_dir required"}

    src = Path(source_dir)
    if not src.exists() or not src.is_dir():
        return {"error": f"source directory not found: {source_dir}"}

    required = ["SOUL.md", "SKILL.md", "style.yaml"]
    found = [f for f in required if (src / f).exists()]
    missing = [f for f in required if f not in found]

    return {"found": found, "missing": missing}


@app.post("/api/persona-studio/import")
async def import_persona(req: ImportPersonaRequest):
    """指定フォルダからSOUL.md/SKILL.md/style.yamlを読み込んで登録する。"""
    import shutil

    persona_id = req.persona_id.strip()
    source_dir = req.source_dir.strip()

    if not persona_id:
        return {"error": "persona_id required"}
    if not source_dir:
        return {"error": "source_dir required"}

    validate_persona_id(persona_id)
    src = Path(source_dir)
    if not src.exists() or not src.is_dir():
        return {"error": f"source directory not found: {source_dir}"}

    dest = PERSONAS_DIR / persona_id
    dest.mkdir(parents=True, exist_ok=True)

    imported = []
    for fname in ("SOUL.md", "SKILL.md", "style.yaml"):
        sf = src / fname
        if sf.exists():
            shutil.copy2(sf, dest / fname)
            imported.append(fname)

    if not imported:
        return {"error": "no SOUL.md, SKILL.md, or style.yaml found in source directory"}

    logger.info("persona imported: %s ← %s (%s)", persona_id, source_dir, ", ".join(imported))
    return {"status": "ok", "persona_id": persona_id, "imported": imported}


@app.get("/api/persona-studio/load/{persona_id}")
async def load_persona(persona_id: str):
    """保存済みペルソナの SOUL.md / SKILL.md / style.yaml を読み込んで返す。"""
    validate_persona_id(persona_id)
    persona_dir = PERSONAS_DIR / persona_id
    if not persona_dir.exists():
        return {"error": f"persona '{persona_id}' not found"}

    draft = {"persona_id": persona_id, "soul_md": "", "skill_md": "", "style": {}}

    soul_path = persona_dir / "SOUL.md"
    if soul_path.exists():
        draft["soul_md"] = soul_path.read_text(encoding="utf-8")

    skill_path = persona_dir / "SKILL.md"
    if skill_path.exists():
        draft["skill_md"] = skill_path.read_text(encoding="utf-8")

    style_path = persona_dir / "style.yaml"
    if style_path.exists():
        raw = load_style_yaml(style_path)
        if raw:
            draft["style"] = raw.get("style", {})

    return {"status": "ok", "draft": draft}


@app.delete("/api/persona-studio/delete/{persona_id}")
async def delete_persona(persona_id: str):
    """ペルソナディレクトリを削除する。"""
    import shutil
    validate_persona_id(persona_id)
    persona_dir = PERSONAS_DIR / persona_id
    if not persona_dir.exists():
        return {"error": f"persona '{persona_id}' not found"}

    try:
        shutil.rmtree(persona_dir)
        logger.info("persona deleted: %s", persona_id)
        return {"status": "ok", "persona_id": persona_id}
    except Exception as e:
        logger.error("delete failed: %s", e)
        return {"error": str(e)}


# ── SSE チャット ─────────────────────────────────────────────────

from fastapi.responses import StreamingResponse


@app.post("/api/chat/cancel")
async def cancel_chat():
    """現在の生成を中断する。"""
    _cancel_event.set()
    return {"status": "ok"}


@app.post("/api/chat")
async def chat_sse(data: dict):
    """SSE ストリーミングでチャット応答を返す。"""
    await _api_lock.acquire()
    try:
        user_text = str(data.get("text", "")).strip()
        if not user_text:
            _api_lock.release()
            return {"error": "empty message"}

        # DEBUG: active が空になっていないか監視
        if not persona_manager.active:
            logger.warning("chat_sse: persona_manager.active is empty! (None or '')")

        # persona_id 検証: 不一致なら自動でセッションを復元
        expected_persona = str(data.get("persona_id", "")).strip()
        expected_session = str(data.get("session_id", "")).strip()
        err = await _auto_resume_session(expected_persona, expected_session)
        if err:
            from fastapi.responses import JSONResponse
            _api_lock.release()
            return JSONResponse(status_code=409, content={"error": "session_mismatch", "detail": err})

        rebuild_system_prompt()
        active_style = persona_manager.get_active_style()
        logger.info("user input  | chars=%d", len(user_text))

        ctx = SessionContext(
            persona_id=persona_manager.active,
            style=active_style or {},
            history=history,
            memory_scope=_get_current_memory_scope(),
        )
        ctx.user_input = user_text

        # hook: on_user_message
        result = await plugin_manager.dispatch("on_user_message", ctx)
        if result is not None:
            ctx = result
        logger.debug("user text   | %s", ctx.user_input[:80])

        # 履歴にユーザー発言追加（再送信の場合は既に履歴にあるのでスキップ）
        is_resend = data.get("resend", False)
        if not is_resend:
            history.add(ctx.user_input, "")
        elif history._messages and history._messages[-1].get("role") == "user":
            history._messages.append({"role": "assistant", "content": ""})

        # hook: on_build_context
        context_messages = history.get_context()
        context_messages = await plugin_manager.dispatch(
            "on_build_context", context_messages, ctx
        )
        context_messages = await plugin_manager.dispatch(
            "on_before_request", context_messages, ctx
        )

        ctx_chars = sum(len(m.get("content", "")) for m in context_messages)
        ctx_tokens_est = int(ctx_chars * 1.5)
        logger.info(
            "api call    | model=%s  msgs=%d  chars=%d  ~tokens=%d",
            config["active_model"], len(context_messages), ctx_chars, ctx_tokens_est,
        )

        async def generate():
            try:
                response_text = ""
                was_cancelled = False
                state_buffer = ""
                in_state = False
                pending = ""
                t_start = time.perf_counter()
                model_info = {}
                _cancel_event.clear()  # 前回のキャンセル状態をリセット
                try:
                    async for chunk in chat_stream(context_messages, config, model_info):
                        if _cancel_event.is_set():
                            was_cancelled = True
                            yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
                            response_text += "\n[中断]"
                            break
                        response_text += chunk
                        if in_state:
                            state_buffer += chunk
                            continue

                        # pending バッファと結合して ---STATE--- を検出（チャンク跨ぎ対応）
                        combined = pending + chunk
                        if "---STATE---" in combined:
                            parts = combined.split("---STATE---", 1)
                            if parts[0]:
                                yield f"data: {json.dumps({'type': 'chunk', 'content': parts[0]}, ensure_ascii=False)}\n\n"
                            in_state = True
                            state_buffer = parts[1] if len(parts) > 1 else ""
                            pending = ""
                        else:
                            # 末尾12文字を保留（"---STATE---" の部分一致の可能性）
                            safe_len = max(0, len(combined) - 12)
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chunk', 'content': combined[:safe_len]}, ensure_ascii=False)}\n\n"
                            pending = combined[safe_len:]

                    # ストリーム終了時に保留中のテキストをフラッシュ
                    if pending and not in_state:
                        yield f"data: {json.dumps({'type': 'chunk', 'content': pending}, ensure_ascii=False)}\n\n"

                except asyncio.CancelledError:
                    partial_text = response_text.split("---STATE---", 1)[0].rstrip()
                    if partial_text:
                        partial_text += "\n[\u4e2d\u65ad]"
                    else:
                        partial_text = "[\u4e2d\u65ad]"
                    if history._messages and history._messages[-1].get("role") == "assistant":
                        history._messages[-1]["content"] = partial_text
                    history.save_turn(force=True)
                    await plugin_manager.dispatch(
                        "on_response_complete", partial_text, ctx)
                    raise
                except Exception as e:
                    elapsed = (time.perf_counter() - t_start) * 1000
                    logger.error("api error   | %.0fms  %s\n%s", elapsed, e, traceback.format_exc())
                    error_payload = {"type": "error"}
                    if isinstance(e, httpx.HTTPStatusError):
                        status = e.response.status_code
                        if status == 401:
                            api_key_val = config.get("providers", {}).get(
                                config.get("active_provider", ""), {}
                            ).get("api_key", "")
                            error_payload["code"] = "api_key_missing" if not api_key_val else "api_unauthorized"
                        else:
                            error_payload["code"] = "api_unknown"
                    elif isinstance(e, httpx.TimeoutException):
                        error_payload["code"] = "api_timeout"
                    elif isinstance(e, httpx.NetworkError):
                        error_payload["code"] = "api_network"
                    elif isinstance(e, (httpx.LocalProtocolError,)):
                        error_payload["code"] = "api_key_missing"
                    else:
                        error_payload["code"] = "api_unknown"
                        error_payload["content"] = str(e)
                    yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
                    # エラー時: ユーザー発言は保存、AI応答はエラーマーカー付きで保存
                    if history._messages and history._messages[-1].get("role") == "assistant":
                        history._messages[-1]["content"] = f"[ERROR: {error_payload.get('code', 'unknown')}]"
                    history.save_turn(force=True)
                    await plugin_manager.dispatch(
                        "on_response_complete",
                        f"[ERROR: {error_payload.get('code', 'unknown')}]",
                        ctx,
                    )
                    return

                elapsed = (time.perf_counter() - t_start) * 1000
                requested = model_info.get("requested", "")
                actual = model_info.get("actual", "")
                # モデル名不一致はOpenRouter/OpenCode Zenでは正常（ランダムモデル/ステルスモデル）
                provider_id = config.get("active_provider", "")
                allow_mismatch = provider_id in ("openrouter", "opencode-zen")
                mismatch = ""
                if actual and not allow_mismatch:
                    req_base = requested.rstrip(":free").split(":")[0]
                    if not actual.startswith(req_base):
                        mismatch = " ***DIFF***"
                logger.info(
                    "api done    | chars=%d  %.0fms  actual=%s%s",
                    len(response_text), elapsed, actual or "?", mismatch,
                )

                # ---STATE--- 抽出と保存
                display_text = response_text
                state_dict = None
                state_update_received = False
                state_overflowed = False
                state_text = state_buffer.strip()
                if state_text:
                    # 表示用テキストから ---STATE--- 以降を除去
                    if "---STATE---" in display_text:
                        display_text = display_text.split("---STATE---", 1)[0].rstrip()

                    # 状態をパース（フラットな key: value）
                    state_dict = {}
                    resolved_keys = set()
                    for line in state_text.split("\n"):
                        line = line.strip()
                        if not line.startswith("- ") or ":" not in line:
                            continue
                        content = line[2:].strip()
                        key, _, val = content.partition(":")
                        key = key.strip()
                        val = val.strip()
                        if key and val == "[解決]":
                            resolved_keys.add(key)
                        elif key:
                            state_dict[key] = val

                    if state_dict or resolved_keys:
                        state_update_received = True
                        previous_state = {}
                        if _state_path().exists():
                            try:
                                loaded_state = json.loads(_state_path().read_text(encoding="utf-8"))
                                if isinstance(loaded_state, dict):
                                    previous_state = loaded_state
                            except Exception:
                                logger.warning("existing state ignored during merge: %s", _state_path().name)
                        state_dict = _merge_state_update(previous_state, state_dict, resolved_keys)
                        state_dict = _bounded_state(state_dict)
                        if state_dict is None:
                            state_overflowed = True
                            _state_tracking.note_overflow()
                            logger.warning("STATE update rejected: exceeds %d chars", MAX_STATE_LENGTH)
                        # 前回状態を読み込み
                        old_state = {}
                        sp = _state_path()
                        if sp.exists():
                            try:
                                old_state = json.loads(sp.read_text(encoding="utf-8"))
                            except Exception:
                                pass

                        # 差分計算 → 保存 → フロントへ送信
                        if state_dict is not None:
                            diff = _diff_state(old_state, state_dict)
                            _save_session_state(state_dict)
                            yield f"data: {json.dumps({'type': 'state', 'state': diff}, ensure_ascii=False)}\n\n"

                # 履歴保存（---STATE--- を除いた本文のみ）
                if history._messages and history._messages[-1]["role"] == "assistant":
                    history._messages[-1]["content"] = display_text
                if state_update_received and not state_overflowed:
                    _state_tracking.note_valid_state()
                    _record_state_snapshot(state_dict)
                elif not was_cancelled and not state_overflowed:
                    _state_tracking.note_missing_state()
                    logger.warning("STATE missing consecutively: %d", _state_tracking.missing_count)
                history.save_turn(force=was_cancelled)

                touch_last_response()
                logger.info("history save| file=%s", history.today_file.name)

                # hook
                await plugin_manager.dispatch("on_response_complete", response_text, ctx)

                if plugin_manager.has("watchdog") and plugin_manager.get("watchdog")._enabled:
                    try:
                        texts = await generate_escalation_texts(config)
                        if texts:
                            plugin_manager.get("watchdog").set_escalation_texts(texts)
                    except Exception:
                        pass

                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"


            finally:
                _api_lock.release()

        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception:
        _api_lock.release()
        raise


# ── フロントエンド（StaticFiles配信 + クリーンURL）─────────────

from fastapi.responses import FileResponse

@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/sessions")

@app.get("/sessions")
async def sessions_page():
    return FileResponse(FRONTEND_DIR / "sessions.html")

@app.get("/chat")
async def chat_page():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/setup")
async def setup_page():
    return FileResponse(FRONTEND_DIR / "session-setup.html")

@app.get("/settings")
async def settings_page():
    return FileResponse(FRONTEND_DIR / "settings.html")

@app.get("/studio")
async def studio_page():
    return FileResponse(FRONTEND_DIR / "studio.html")


# ── 起動 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
