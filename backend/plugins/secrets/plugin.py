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
import os
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
            temp_path = self._store_path.with_suffix(self._store_path.suffix + ".tmp")
            try:
                temp_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temp_path, self._store_path)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

    def register(self, value: str, label: str = "") -> str:
        """新しい機密値を登録し、プレースホルダーを返す。"""
        sid = str(self._next_id)
        self._next_id += 1
        self._secrets[sid] = {"value": value, "label": label}
        self._save()
        logger.info("secrets: registered id=%s", sid)
        return f"{{{{secret:{sid}}}}}"

    def normalize_text(self, text: str) -> str:
        """既存の {{s: label: value}} 構文をプレースホルダー化する。"""
        def replacer(m):
            label = (m.group(1) or "").strip()
            value = m.group(2).strip()
            if not value:
                return m.group(0)
            return self.register(value, label)

        return SECRET_INPUT_RE.sub(replacer, text)

    def protect_text(self, text: str) -> str:
        """入力構文と登録済み実値を外部送信可能な表現へ置換する。"""
        protected = self.normalize_text(text)
        values = sorted(
            ((entry.get("value", ""), f"{{{{secret:{sid}}}}}")
             for sid, entry in self._secrets.items()),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for value, placeholder in values:
            if value:
                protected = protected.replace(value, placeholder)
        return protected

    def get_entry(self, placeholder: str) -> dict | None:
        """完全一致するプレースホルダーの登録情報を返す。"""
        m = PLACEHOLDER_RE.fullmatch(placeholder)
        return self._secrets.get(m.group(1)) if m else None
    def reveal(self, placeholder: str) -> str:
        """プレースホルダーから実値を取得。"""
        entry = self.get_entry(placeholder)
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

        new_text = self.protect_text(text)

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
            protected = self.protect_text(content)
            if protected != content:
                leaked += 1
            msg["content"] = protected

        if leaked:
            logger.warning("secrets: fixed %d leak(s) in context", leaked)
        return messages
