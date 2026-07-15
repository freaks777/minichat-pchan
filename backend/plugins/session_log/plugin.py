"""session_log プラグイン — セッション終了時に会話ログを保存。

on_session_end で履歴を Markdown 形式に変換し、
session-log/{persona_id}/ 配下に日付ファイルとして出力する。
memory プラグインが有効な場合は、ファクト抽出の入力としても活用される。
"""

import logging
import time
from pathlib import Path

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")


class SessionLogPlugin(PluginBase):
    name = "session_log"
    hooks = ["on_session_end"]
    priority = 80  # 後処理系の先頭
    critical = False

    def __init__(self):
        self._log_dir: Path | None = None

    def set_log_dir(self, path: Path):
        """ログ出力先ディレクトリを設定。main.py 起動時に呼ばれる。"""
        self._log_dir = path

    async def run(self, hook: str, data, ctx):
        if hook == "on_session_end":
            await self._write_log(ctx)
        return data

    async def _write_log(self, ctx):
        """セッションの会話ログを Markdown で保存。"""
        if self._log_dir is None:
            logger.warning("session_log: log_dir not set, skipping")
            return

        history = ctx.history
        persona_id = ctx.persona_id

        messages = getattr(history, "_messages", [])
        if not messages:
            logger.info("session_log: no messages to save")
            return

        # 出力先
        today = getattr(history, "_session_date", "") or time.strftime("%Y-%m-%d")
        sid = getattr(history, "session_id", "") or f"{time.strftime('%H%M%S')}00"
        out_dir = self._log_dir / persona_id
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{today}_{sid}.md"
        out_path = out_dir / fname

        # Markdown 構築
        lines = []
        lines.append(f"# Session Log — {persona_id}")
        lines.append(f"")
        lines.append(f"- Date: {today}")
        lines.append(f"- Messages: {len(messages)}")
        lines.append(f"- Persona: {persona_id}")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        for i, msg in enumerate(messages, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"### [{i}] 👤 User")
            elif role == "assistant":
                lines.append(f"### [{i}] 🤖 Assistant")
            elif role == "system":
                continue  # システムプロンプトは出力しない
            else:
                lines.append(f"### [{i}] {role}")
            lines.append(f"")
            lines.append(content)
            lines.append(f"")

        # The rendered log contains the full history, so overwrite idempotently.
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(
            "session_log: saved %d messages → %s",
            len(messages), out_path,
        )
