"""
RPスタンドアロンアプリ — FastAPI エントリポイント（コア最小実装）。

起動:
    cd backend
    python main.py                          # 通常（config.yamlのモデル）
    python main.py --debug                  # デバッグログ有効
    python main.py --model MODEL_ID         # モデル上書き（無料モデル等）
    → http://localhost:8765 で起動
    → WebSocket ws://localhost:8765/ws/chat でチャット可能

テスト用簡易HTMLフロント: http://localhost:8765/
"""

import json
import logging
import os
import re
import sys
import time
import traceback
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

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

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import load_config, update_config_yaml
from core.api import chat_stream, chat_sync
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
        plugin_manager.get("secrets").configure(str(store_path))
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

    yield
    # 現在のセッションを終了（session_log + memory の事実抽出）
    if persona_manager.active:
        try:
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


app = FastAPI(title="RP Standalone", lifespan=lifespan)

# ── 設定読込 ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

# ── 静的ファイル配信（フロントエンドSPA） ──────────────────────
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if not FRONTEND_DIR.exists():
    logger.warning("frontend/ directory not found at %s — static files unavailable", FRONTEND_DIR)
app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
config = load_config(CONFIG_PATH)

if MODEL_OVERRIDE:
    config["active_model"] = MODEL_OVERRIDE
    logger.info("model override: %s", MODEL_OVERRIDE)

logger.info("model: %s", config["active_model"])

# ── 起動時バリデーション ──────────────────────────────────────────
if not _env_path:
    logger.warning(".env file not found — API keys may be missing")

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

# persona_studio にAPI設定を注入
if plugin_manager.has("persona_studio"):
    ps = plugin_manager.get("persona_studio")
    ps.configure(config)
    logger.info("persona_studio configured")


# ── .last-response タイムスタンプファイル ──────────────────────
LAST_RESPONSE = BASE_DIR.parent / ".last-response"


def touch_last_response():
    """最終応答時刻を更新（watchdog用）。"""
    LAST_RESPONSE.write_text(str(time.time()))


def rebuild_system_prompt():
    """スタイル変更後にシステムプロンプトを再構築する。"""
    global system_messages
    system_messages = persona_manager.get_system_prompt()

    # 現在の状態を注入
    state_text = _load_session_state()
    if state_text:
        system_messages.append({"role": "system", "content": state_text})

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
        "- 【重要】ユーザーの入力が現在の状態と矛盾する・物理的に不可能な場合、\n"
        "  それを受け入れてはいけない。状態に基づいて現実的な反応を返すこと"
    )})

    history.set_system_prompt(system_messages)


def _state_path() -> Path:
    """現在のセッションの状態ファイルパスを返す。"""
    sid = history.session_id or "00000000"
    return BASE_DIR.parent / "sessions" / persona_manager.active / f"{sid}_state.json"


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


def _save_session_state(state: dict):
    """現在の状態を _state.json に保存（フル上書き）。"""
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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


async def _dispatch_session_end_for_active():
    """現在アクティブなペルソナのセッション終了フックを発火。"""
    if persona_manager.active:
        try:
            old_ctx = SessionContext(
                persona_id=persona_manager.active,
                style=persona_manager.get_active_style() or {},
                history=history,
            )
            await plugin_manager.dispatch("on_session_end", None, old_ctx)
        except Exception:
            logger.exception("on_session_end dispatch failed")


def _activate_session(persona_id: str, session_id: str,
                      jsonl_path: Path | None = None):
    """ペルソナ切替 + 履歴ロード + スタイルロック + システムプロンプト再構築 + 状態保存。"""
    persona_manager.switch(persona_id)
    history.reload(persona_id)

    if jsonl_path and jsonl_path.exists():
        history._load_specific(jsonl_path)
    else:
        history._messages = []
        history._turn_count = 0

    history.set_session_id(session_id)

    try:
        persona_manager.start_session()
    except ValueError:
        persona_manager.start_session(
            {"viewpoint": "ai_character", "person": "first", "narration": True}
        )

    rebuild_system_prompt()

    session_state = {
        "persona_id": persona_id,
        "session_id": history.session_id,
        "style": persona_manager.get_active_style(),
        "started_at": time.time(),
    }
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


# ── REST API ─────────────────────────────────────────────────────

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
async def set_provider(data: dict):
    """プロバイダとモデルを切り替える（config.yaml 書き戻し）。
    models 配列が渡された場合は当該プロバイダのモデルリストも更新する。
    model が空の場合は models の先頭を active_model として使用する。
    """
    provider = data.get("provider", "")
    model = data.get("model", "")
    models = data.get("models", None)

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

    # メモリ上の config も更新
    config["active_provider"] = provider
    config["active_model"] = model
    config["providers"][provider]["models"] = models

    logger.info("config updated: provider=%s model=%s models=%d", provider, model, len(models))
    return {"status": "ok", "active_provider": provider, "active_model": model, "models": models}


@app.post("/api/config/api")
async def set_api_params(data: dict):
    """API 共通パラメータを更新。"""
    api = data.get("api", {})
    allowed = {"max_tokens", "temperature", "timeout"}
    update = {k: v for k, v in api.items() if k in allowed}

    def mutator(raw: dict):
        raw.setdefault("api", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)

    config["api"].update(update)
    logger.info("api params updated: %s", update)
    return {"status": "ok", "api": config["api"]}


@app.post("/api/config/watchdog")
async def set_watchdog(data: dict):
    """Watchdog 設定を更新。"""
    watchdog = data.get("watchdog", {})

    def mutator(raw: dict):
        raw["watchdog"] = watchdog

    update_config_yaml(CONFIG_PATH, mutator)

    config["watchdog"] = watchdog

    # 実行中の watchdog プラグインにも反映
    if plugin_manager.has("watchdog"):
        plugin_manager.get("watchdog").configure(watchdog)

    logger.info("watchdog config updated")
    return {"status": "ok", "watchdog": watchdog}


@app.post("/api/config/session")
async def set_session_config(data: dict):
    """セッション設定を更新。"""
    session = data.get("session", {})
    allowed = {"max_tokens", "save_interval"}
    update = {k: v for k, v in session.items() if k in allowed}

    def mutator(raw: dict):
        raw.setdefault("session", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)

    config["session"].update(update)
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
async def set_style(data: dict):
    """グローバル文体設定を更新。"""
    style = data.get("style", {})
    allowed = {"viewpoint", "narration", "person"}
    update = {}
    for k in allowed:
        if k in style:
            val = style[k]
            update[k] = bool(val) if k == "narration" else val

    def mutator(raw: dict):
        raw.setdefault("style", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)

    config.setdefault("style", {}).update(update)
    logger.info("global style updated: %s", update)
    return {"status": "ok", "style": config["style"]}


@app.get("/api/persona/list")
async def list_personas():
    return persona_manager.list_personas()


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

        # JSONL ファイルを日付降順で取得
        jsonl_files = sorted(persona_dir.glob("*.jsonl"), reverse=True)
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
                today = time.strftime("%Y-%m-%d")
                # 既存リストに同じセッションがあるか確認
                existing_ids = {s["id"] for s in sessions}
                current_id = f"{pid}/{today}_{sid}"
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


@app.delete("/api/sessions/{persona_id}/{date}")
async def delete_session(persona_id: str, date: str):
    """セッションのJSONLファイルを削除する。"""
    validate_persona_id(persona_id)
    # ファイル名は常に YYYY-MM-DD_HHMMSSRR 形式
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", date):
        return {"error": "invalid format (use YYYY-MM-DD_HHMMSSRR)"}

    file_path = BASE_DIR.parent / "sessions" / persona_id / f"{date}.jsonl"

    if not file_path.exists():
        # JSONLファイルがない場合、.current-session を確認
        current_path = BASE_DIR / ".current-session"
        if current_path.exists():
            try:
                current = json.loads(current_path.read_text(encoding="utf-8"))
                if (current.get("persona_id") == persona_id
                        and current.get("session_id") == date.split("_")[1]):
                    current_path.unlink()
                    logger.info("current session cleared: %s/%s", persona_id, date)
                    return {"status": "ok", "persona_id": persona_id, "date": date}
            except Exception:
                pass
        # ファイルがなく、current-sessionも該当しなければゾンビ掃除扱い
        logger.info("session cleanup (no file): %s/%s", persona_id, date)
        return {"status": "ok", "persona_id": persona_id, "date": date, "note": "no file found"}

    try:
        file_path.unlink()
        logger.info("session deleted: %s/%s", persona_id, date)
        # 空になったペルソナディレクトリも掃除
        persona_dir = file_path.parent
        if persona_dir.exists() and not any(persona_dir.iterdir()):
            persona_dir.rmdir()
        return {"status": "ok"}
    except Exception as e:
        logger.error("session delete failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona/switch")
async def switch_persona(data: dict):
    persona_id = data.get("persona_id", "")
    if not persona_id:
        return {"error": "persona_id required"}
    try:
        validate_persona_id(persona_id)
        persona_manager.switch(persona_id)
        history.reload(persona_id)
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
async def start_session(data: dict):
    """セッションを開始し、スタイルをロックする。

    Body: {"persona_id": "kyouka-detective", "style_override": {...}}
    persona_id が指定された場合はペルソナを切り替える。
    """
    persona_id = data.get("persona_id", "").strip()
    if persona_id:
        # persona_id のバリデーション（パストラバーサル防止、防御的）
        try:
            validate_persona_id(persona_id)
        except ValueError as e:
            return {"error": str(e)}

        try:
            # 前のセッションを終了
            if persona_manager.active and persona_manager.active != persona_id:
                old_ctx = SessionContext(
                    persona_id=persona_manager.active,
                    style=persona_manager.get_active_style() or {},
                    history=history,
                )
                await plugin_manager.dispatch("on_session_end", None, old_ctx)
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
    history.set_session_id(session_id)

    # 新規セッション：JSONLファイルを空にする（同名の旧データが残っていた場合に備える）
    history._save_full()

    try:
        style = persona_manager.start_session(data.get("style_override"))
    except ValueError as e:
        return {"error": str(e)}

    rebuild_system_prompt()

    # セッション状態をファイルに保存
    session_state = {
        "persona_id": persona_manager.active,
        "session_id": session_id,
        "style": style,
        "started_at": time.time(),
    }
    state_path = BASE_DIR / ".current-session"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(session_state, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # hook: on_session_start
    ctx = SessionContext(
        persona_id=persona_manager.active,
        style=style,
        history=history,
    )
    await plugin_manager.dispatch("on_session_start", ctx)

    logger.info(
        "session started | persona=%s style=%s",
        persona_manager.active,
        json.dumps(style, ensure_ascii=False),
    )
    return {"status": "ok", "persona_id": persona_manager.active, "style": style}


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
        "session_id": state.get("session_id", ""),
        "style": style,
        "started_at": state.get("started_at", 0),
    }


@app.get("/api/session/state")
async def get_session_state():
    """現在のセッション状態を返す。"""
    return {"state": json.loads(_state_path().read_text(encoding="utf-8")) if _state_path().exists() else {}}


@app.get("/api/session/history")
async def get_history(persona_id: str = "", session_id: str = ""):
    """現在の履歴メッセージを返す。persona_id指定時は自動復元。"""
    await _auto_resume_session(persona_id, session_id)
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
    if not persona_id or persona_id == persona_manager.active:
        return None  # 一致または空 → 何もしない

    # persona_id のバリデーション（パストラバーサル防止、防御的）
    try:
        validate_persona_id(persona_id)
    except ValueError as e:
        return str(e)

    # session_id のバリデーション（パストラバーサル防止）
    if session_id:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", session_id):
            return f"invalid session_id format: {session_id}"

    logger.info(
        "auto-resume: persona mismatch frontend=%s server=%s",
        persona_id, persona_manager.active,
    )
    try:
        # 前のセッションを終了（session_log + memory の事実抽出）
        await _dispatch_session_end_for_active()

        # 履歴のロードまたは初期化
        if session_id:
            jsonl_path = (
                BASE_DIR.parent / "sessions" / persona_id /
                f"{session_id}.jsonl"
            )
        else:
            jsonl_path = None
            session_id = time.strftime("%H%M%S") + str(__import__("random").randint(10, 99))

        _activate_session(persona_id, session_id, jsonl_path)
        logger.info("auto-resume: success persona=%s session=%s", persona_id, history.session_id)
        return None
    except Exception as e:
        logger.error("auto-resume failed: %s", e)
        return str(e)


@app.post("/api/session/resume")
async def resume_session(data: dict):
    """既存セッションを再開する。

    Body: {"session_id": "kyouka-detective/2026-07-06_HHMMSSRR"}
    """
    raw_id = data.get("session_id", "").strip()
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

    # 前のセッションを終了（別ペルソナに切り替わる場合のみ）
    if persona_manager.active and persona_manager.active != persona_id:
        await _dispatch_session_end_for_active()

    jsonl_path = BASE_DIR.parent / "sessions" / persona_id / f"{file_stem}.jsonl"
    session_id = file_stem.split("_", 1)[1]  # HHMMSSRR 部分を抽出

    try:
        _activate_session(persona_id, session_id, jsonl_path)
    except ValueError as e:
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
    )
    await plugin_manager.dispatch("on_session_start", ctx)

    logger.info("session resumed | persona=%s date=%s", persona_id, file_stem)
    return {"status": "ok", "persona_id": persona_id,
            "style": persona_manager.get_active_style()}


@app.post("/api/session/update-message")
async def update_message(data: dict):
    """指定インデックスのメッセージ内容を更新する。

    Body: {"index": 0, "content": "新しい内容", "persona_id": "...", "session_id": "..."}
    """
    await _auto_resume_session(
        str(data.get("persona_id", "")).strip(),
        str(data.get("session_id", "")).strip(),
    )
    index = data.get("index", -1)
    content = data.get("content", "")
    if not isinstance(index, int) or index < 0 or index >= len(history._messages):
        return {"error": f"invalid index (0-{len(history._messages)-1})"}
    history.update_message(index, content)
    logger.info("message updated: index=%d", index)
    return {"status": "ok"}


@app.post("/api/session/delete-message")
async def delete_message(data: dict):
    """指定インデックスのメッセージを削除する。

    Body: {"index": 0, "persona_id": "...", "session_id": "..."}
    ユーザーメッセージ削除時は対応するアシスタント応答も削除。
    """
    await _auto_resume_session(
        str(data.get("persona_id", "")).strip(),
        str(data.get("session_id", "")).strip(),
    )
    index = data.get("index", -1)
    if not isinstance(index, int) or index < 0 or index >= len(history._messages):
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
    logger.info("message deleted: index=%d role=%s deleted=%d", index, role, deleted)
    return {"status": "ok", "deleted": deleted}


@app.post("/api/session/truncate")
async def truncate_history(data: dict):
    """指定インデックス以降のメッセージをすべて削除する。

    Body: {"from_index": 3, "persona_id": "...", "session_id": "..."}
    from_index のメッセージ自体も削除対象。
    """
    await _auto_resume_session(
        str(data.get("persona_id", "")).strip(),
        str(data.get("session_id", "")).strip(),
    )
    from_index = data.get("from_index", -1)
    if not isinstance(from_index, int) or from_index < 0 or from_index >= len(history._messages):
        return {"error": f"invalid from_index (0-{len(history._messages)-1})"}
    deleted = len(history._messages) - from_index
    history._messages = history._messages[:from_index]
    history._turn_count = sum(1 for m in history._messages if m.get("role") == "user")
    history._save_full()
    logger.info("history truncated: from_index=%d deleted=%d", from_index, deleted)
    return {"status": "ok", "deleted": deleted}


@app.post("/api/session/opening")
async def generate_opening():
    """SOUL.md の「開始時の状況」を読み取って返す。生成はペルソナ作成時に行う。"""
    if not persona_manager.active:
        return {"error": "no active persona"}

    soul_path = persona_manager.active_dir / "SOUL.md"
    if not soul_path.exists():
        return {"status": "ok", "opening": None}

    soul_md = soul_path.read_text(encoding="utf-8")
    import re
    m = re.search(r"##\s*開始時の状況[\s\S]*?(?=\n##\s|\n---|$)", soul_md)
    if not m:
        return {"status": "ok", "opening": None}

    scene = m.group(0).split("\n", 1)[1].strip() if "\n" in m.group(0) else ""
    return {"status": "ok", "opening": scene}


# ── persona_studio API ───────────────────────────────────────────


@app.post("/api/persona-studio/estimate-style")
async def estimate_style(data: dict):
    """SOUL.md テキストから文体を推定する。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    soul_text = data.get("soul_md_text", "")
    if not soul_text:
        return {"error": "soul_md_text required"}
    try:
        result = await plugin_manager.get("persona_studio").estimate_style_from_soul(soul_text)
        return {"status": "ok", "estimate": result}
    except Exception as e:
        logger.error("estimate_style failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/create-template")
async def create_template(data: dict):
    """フォーム入力から SOUL.md / SKILL.md / style を生成。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        draft = await plugin_manager.get("persona_studio").create_via_template(data)
        return {"status": "ok", "draft": draft}
    except Exception as e:
        logger.error("create_template failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/extract-fields")
async def extract_fields(data: dict):
    """自由記述テキストから CharacterData フィールドを抽出（v3.3）。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        result = await plugin_manager.get("persona_studio").extract_fields(
            data.get("text", ""),
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("extract_fields failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/convert-freetext")
async def convert_freetext(data: dict):
    """自由記述テキストをペルソナ形式に変換。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        draft = await plugin_manager.get("persona_studio").convert_freetext(
            data.get("text", ""),
            data.get("style_override"),
        )
        return {"status": "ok", "draft": draft}
    except Exception as e:
        logger.error("convert_freetext failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/refine")
async def refine_draft(data: dict):
    """ドラフトを指示に従って部分修正。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        revised = await plugin_manager.get("persona_studio").refine(
            data.get("draft", {}),
            data.get("instruction", ""),
        )
        return {"status": "ok", "draft": revised}
    except Exception as e:
        logger.error("refine failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/test-chat")
async def test_chat(data: dict):
    """ドラフトのペルソナでテスト会話。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    try:
        response_text = await plugin_manager.get("persona_studio").test_chat(
            data.get("draft", {}),
            data.get("message", ""),
        )
        return {"status": "ok", "response": response_text}
    except Exception as e:
        logger.error("test_chat failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/save")
async def save_persona(data: dict):
    """ドラフトを personas/{persona_id}/ に保存。"""
    if not plugin_manager.has("persona_studio"):
        return {"error": "persona_studio plugin not loaded"}
    persona_id = data.get("persona_id", "").strip()
    if not persona_id:
        return {"error": "persona_id required"}
    try:
        validate_persona_id(persona_id)
        plugin_manager.get("persona_studio").save(
            PERSONAS_DIR, persona_id, data.get("draft", {})
        )
        logger.info("persona saved: %s", persona_id)
        return {"status": "ok", "persona_id": persona_id}
    except Exception as e:
        logger.error("save failed: %s", e)
        return {"error": str(e)}


@app.post("/api/persona-studio/validate-files")
async def validate_files(data: dict):
    """指定フォルダ内のペルソナファイルの有無を確認する。"""
    source_dir = data.get("source_dir", "").strip()
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
async def import_persona(data: dict):
    """指定フォルダからSOUL.md/SKILL.md/style.yamlを読み込んで登録する。"""
    import shutil

    persona_id = data.get("persona_id", "").strip()
    source_dir = data.get("source_dir", "").strip()

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


@app.post("/api/chat")
async def chat_sse(data: dict):
    """SSE ストリーミングでチャット応答を返す。"""
    user_text = str(data.get("text", "")).strip()
    if not user_text:
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
        return JSONResponse(status_code=409, content={"error": "session_mismatch", "detail": err})

    active_style = persona_manager.get_active_style()
    logger.info("user input  | chars=%d", len(user_text))
    logger.debug("user text   | %s", user_text[:80])

    ctx = SessionContext(
        persona_id=persona_manager.active,
        style=active_style or {},
        history=history,
    )
    ctx.user_input = user_text

    # hook: on_user_message
    result = await plugin_manager.dispatch("on_user_message", ctx)
    if result is not None:
        ctx = result

    # 履歴にユーザー発言追加（再送信の場合は既に履歴にあるのでスキップ）
    is_resend = data.get("resend", False)
    if not is_resend:
        history.add(user_text, "")
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
        response_text = ""
        state_buffer = ""  # ---STATE--- 以降を蓄積
        in_state = False
        pending = ""       # 前チャンク末尾（---STATE--- のチャンク跨ぎ検出用）
        t_start = time.perf_counter()
        model_info = {}
        try:
            async for chunk in chat_stream(context_messages, config, model_info):
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
            # エラー時は履歴から追加分を除去
            if len(history._messages) >= 2:
                history._messages.pop()
                history._messages.pop()
            return

        elapsed = (time.perf_counter() - t_start) * 1000
        requested = model_info.get("requested", "")
        actual = model_info.get("actual", "")
        mismatch = (
            " ***DIFF***" if actual and not actual.startswith(
                requested.rstrip(":free").split(":")[0]
            ) else ""
        )
        logger.info(
            "api done    | chars=%d  %.0fms  actual=%s%s",
            len(response_text), elapsed, actual or "?", mismatch,
        )

        # ---STATE--- 抽出と保存
        display_text = response_text
        state_text = state_buffer.strip()
        if state_text:
            # 表示用テキストから ---STATE--- 以降を除去
            if "---STATE---" in display_text:
                display_text = display_text.split("---STATE---", 1)[0].rstrip()

            # 状態をパース（フラットな key: value）
            state_dict = {}
            for line in state_text.split("\n"):
                line = line.strip()
                if not line.startswith("- ") or ":" not in line:
                    continue
                content = line[2:].strip()
                key, _, val = content.partition(":")
                key = key.strip()
                val = val.strip()
                if key:
                    state_dict[key] = val

            if state_dict:
                # 前回状態を読み込み
                old_state = {}
                sp = _state_path()
                if sp.exists():
                    try:
                        old_state = json.loads(sp.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                # 差分計算 → 保存 → フロントへ送信
                diff = _diff_state(old_state, state_dict)
                _save_session_state(state_dict)
                yield f"data: {json.dumps({'type': 'state', 'state': diff}, ensure_ascii=False)}\n\n"

        # 履歴保存（---STATE--- を除いた本文のみ）
        if history._messages and history._messages[-1]["role"] == "assistant":
            history._messages[-1]["content"] = display_text
        history.save_turn()

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

    return StreamingResponse(generate(), media_type="text/event-stream")


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
