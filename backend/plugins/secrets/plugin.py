"""secrets プラグイン — 機密情報のプレースホルダー化。

ユーザーが {{s: 実値}} で入力した値を登録し、以後のAPI送信・履歴保存では
{{secret:N}} のプレースホルダーに置換する。実値はローカルJSONに平文保管し、
画面表示時にのみ展開する。

hook:
  on_user_message: 入力中の {{s:...}} を検出→登録→プレースホルダー化
  on_build_context: 全メッセージから実値のリークを検出→置換
"""

import json
import logging
import re
from pathlib import Path

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")

# 入力構文: {{s: label: value}} または {{s: value}}
SECRET_INPUT_RE = re.compile(r"\{\{s:\s*(?:(\w+):\s*)?(.+?)\}\}")

# プレースホルダー構文
PLACEHOLDER_RE = re.compile(r"\{\{secret:(\d+)\}\}")


class SecretsPlugin(PluginBase):
    name = "secrets"
    hooks = ["on_user_message", "on_build_context"]
    priority = 10   # 全プラグインの先頭でマスキング
    critical = True  # 失敗＝チャット中断（漏洩防止）

    def __init__(self):
        self._store_path: Path | None = None
        self._secrets: dict[str, dict] = {}  # id → {value, label}
        self._next_id: int = 1

    def configure(self, store_path: str):
        """保存先パスを設定し、既存データを読み込む。"""
        self._store_path = Path(store_path)
        self._load()

    def _load(self):
        if self._store_path and self._store_path.exists():
            try:
                data = json.loads(self._store_path.read_text(encoding="utf-8"))
                self._secrets = data.get("secrets", {})
                self._next_id = data.get("next_id", 1)
                logger.info("secrets: loaded %d entries", len(self._secrets))
            except Exception as e:
                logger.error("secrets: load failed (%s)", e)

    def _save(self):
        if self._store_path:
            data = {"secrets": self._secrets, "next_id": self._next_id}
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def register(self, value: str, label: str = "") -> str:
        """新しい機密値を登録し、プレースホルダーを返す。"""
        sid = str(self._next_id)
        self._next_id += 1
        self._secrets[sid] = {"value": value, "label": label}
        self._save()
        logger.info("secrets: registered id=%s label=%s", sid, label or "-")
        return f"{{{{secret:{sid}}}}}"

    def reveal(self, placeholder: str) -> str:
        """プレースホルダーから実値を取得。"""
        m = PLACEHOLDER_RE.match(placeholder)
        if m:
            entry = self._secrets.get(m.group(1))
            if entry:
                return entry["value"]
        return placeholder  # 不明なプレースホルダーはそのまま

    # ── hooks ──────────────────────────────────────────────────

    async def run(self, hook: str, data, ctx):
        if hook == "on_user_message":
            return self._mask_input(data)
        elif hook == "on_build_context":
            return self._check_leak(data)
        return data

    def _mask_input(self, ctx):
        """ユーザー入力から {{s:...}} を検出→登録→プレースホルダー化。"""
        text = ctx.user_input

        def replacer(m):
            label = (m.group(1) or "").strip()
            value = m.group(2).strip()
            if not value:
                return m.group(0)
            return self.register(value, label)

        new_text = SECRET_INPUT_RE.sub(replacer, text)
        if new_text != text:
            ctx.user_input = new_text
            logger.debug("secrets: masked input")
        return ctx

    def _check_leak(self, messages: list[dict]) -> list[dict]:
        """全メッセージから実値のリークを検出し、プレースホルダーに置換。"""
        # 実値→プレースホルダーの逆引きマップ
        value_to_placeholder = {}
        for sid, entry in self._secrets.items():
            val = entry["value"]
            if val:
                value_to_placeholder[val] = f"{{{{secret:{sid}}}}}"

        if not value_to_placeholder:
            return messages

        leaked = 0
        for msg in messages:
            content = msg.get("content", "")
            for real_value, placeholder in value_to_placeholder.items():
                if real_value in content:
                    content = content.replace(real_value, placeholder)
                    leaked += 1
            msg["content"] = content

        if leaked:
            logger.warning("secrets: fixed %d leak(s) in context", leaked)
        return messages
