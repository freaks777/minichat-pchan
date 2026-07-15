"""watchdog プラグイン — ユーザー放置検知＋メール通知。

on_user_message / on_session_start で最終操作時刻を更新し、
バックグラウンドループで経過時間を監視する。
タイムアウト時に mail プラグイン経由でエスカレーション通知を送る。

設定は config.yaml の watchdog セクション:
  watchdog:
    check_interval: 30       # 監視ループ間隔（秒）
    levels:
      - after: 300           # 経過秒
        subject: "件名"
        body: "本文"
"""

import asyncio
import logging
import time

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")


class WatchdogPlugin(PluginBase):
    name = "watchdog"
    hooks = ["on_session_start", "on_user_message", "on_session_end"]
    priority = 20  # 早めに実行（他プラグインより先に検知）
    critical = False

    def __init__(self):
        self._last_activity = 0.0
        self._current_level = 0   # 次に発火すべきエスカレーションレベル
        self._task: asyncio.Task | None = None
        self._running = False
        self._mail_plugin = None  # main.py から注入される
        self._levels = []         # [(after_seconds, subject, body), ...]
        self._check_interval = 30
        self._enabled = True

    def set_mail_plugin(self, mail_plugin):
        """mail プラグインの参照を受け取る。main.py 起動時に呼ばれる。"""
        self._mail_plugin = mail_plugin

    def configure(self, config: dict | None = None):
        """config.yaml の watchdog セクションから設定を読み込む。"""
        if config is None:
            config = {}
        self._enabled = config.get("enabled", True)
        self._check_interval = config.get("check_interval", 30)
        self._levels = [
            (lv.get("after", 300), lv.get("subject", ""), lv.get("body", ""))
            for lv in config.get("levels", [])
        ]
        if self._enabled:
            self._ensure_monitor()
        else:
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()

    def set_escalation_texts(self, levels: list[dict]):
        """RP文脈に合わせて動的生成されたエスカレーション文面を注入する。

        Args:
            levels: [{"after": 300, "subject": "声かけ", "body": "..."}, ...]

        注入しても _current_level はリセットされない（既存の進行度を維持）。
        新規セッション開始時に _reset() されるため、次回は新文面で動作する。
        """
        self._levels = [
            (lv.get("after", 300), lv.get("subject", ""), lv.get("body", ""))
            for lv in levels
        ]
        logger.info(
            "watchdog: escalation texts updated (levels=%d)", len(self._levels),
        )

    async def initialize(self):
        if not self._enabled:
            logger.info("watchdog: disabled by config — not starting monitor")
            return
        self._ensure_monitor()

    async def run(self, hook: str, data, ctx):
        if hook == "on_session_start":
            self._ensure_monitor()
            self._reset()
        elif hook == "on_user_message":
            self._reset()
        elif hook == "on_session_end":
            self._last_activity = 0.0
            self._current_level = 0
        return data

    async def shutdown(self):
        """監視ループを停止し、タスクの終了を待つ。"""
        await self._stop_monitor()


    def _ensure_monitor(self):
        """Start the monitor exactly once when enabled."""
        if not self._enabled or (self._task and not self._task.done()):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "watchdog: started (levels=%d, interval=%ds)",
            len(self._levels), self._check_interval,
        )

    async def _stop_monitor(self):
        """監視タスクをキャンセルし、完了を待機する。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _reset(self):
        """最終操作時刻とエスカレーションレベルをリセット。"""
        self._last_activity = time.time()
        self._current_level = 0

    async def _monitor_loop(self):
        """バックグラウンド監視ループ。"""
        try:
            while self._running:
                await asyncio.sleep(self._check_interval)
                if not self._running:
                    break
                await self._check()
        except asyncio.CancelledError:
            logger.info("watchdog: monitor loop cancelled")

    async def _check(self):
        """経過時間を確認し、必要ならエスカレーション。"""
        if self._last_activity == 0.0:
            return

        elapsed = time.time() - self._last_activity

        # 現在のレベルに対応する閾値を超えているか確認
        while self._current_level < len(self._levels):
            threshold, subject, body = self._levels[self._current_level]
            if elapsed >= threshold:
                await self._escalate(self._current_level, subject, body)
                self._current_level += 1
            else:
                break

    async def _escalate(self, level: int, subject: str, body: str):
        """エスカレーション通知を送信。"""
        logger.warning("watchdog: escalation Lv%d (%.0fs)", level + 1,
                       time.time() - self._last_activity)

        if self._mail_plugin is None:
            logger.error("watchdog: mail plugin not wired, cannot send")
            return

        full_subject = f"[Lv{level + 1}] {subject}"
        self._mail_plugin.send(body, full_subject)
