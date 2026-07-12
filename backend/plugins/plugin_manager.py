"""プラグインマネージャ。hookディスパッチとプラグイン読み込み。"""

import importlib
import logging
from pathlib import Path
from typing import Any

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")


class PluginManager:
    HOOKS = [
        "on_session_start",
        "on_user_message",
        "on_build_context",
        "on_before_request",
        "on_response_complete",
        "on_persona_switch",
        "on_session_end",
    ]

    def __init__(self, enabled_plugins: list[str], plugins_dir: str | Path = None):
        self.plugins: list[PluginBase] = []
        if plugins_dir is None:
            plugins_dir = Path(__file__).resolve().parent
        else:
            plugins_dir = Path(plugins_dir)
        self._plugins_dir = plugins_dir
        for name in enabled_plugins:
            self._load(name)
        self._sort_by_priority()

    def _sort_by_priority(self):
        """priority の昇順（小さい方が先）にソート。同値はロード順を維持。"""
        self.plugins.sort(key=lambda p: p.priority)

    def _load(self, name: str):
        """プラグインを動的に読み込む。

        plugins/{name}/plugin.py を importlib でロードし、
        PluginBase を継承したクラスのインスタンスを生成する。
        """
        module_path = f"plugins.{name}.plugin"
        try:
            module = importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError) as e:
            logger.error("plugin load failed: %s (%s)", name, e)
            return

        # PluginBase のサブクラスを探す
        plugin_instance = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginBase)
                and attr is not PluginBase
            ):
                plugin_instance = attr()
                break

        if plugin_instance is None:
            logger.error("plugin load failed: %s (no PluginBase subclass found)", name)
            return

        if not plugin_instance.name:
            plugin_instance.name = name

        self.plugins.append(plugin_instance)
        logger.info(
            "plugin loaded: %s  hooks=%s  priority=%d  critical=%s",
            plugin_instance.name,
            plugin_instance.hooks,
            plugin_instance.priority,
            plugin_instance.critical,
        )

    async def initialize_all(self):
        """全プラグインの initialize() を呼ぶ。

        initialize() の失敗は起動時の致命的エラーとして扱い、再raiseする。
        """
        for plugin in self.plugins:
            try:
                await plugin.initialize()
                logger.info("plugin initialized: %s", plugin.name)
            except Exception:
                logger.exception("plugin init failed: %s", plugin.name)
                raise

    async def shutdown_all(self):
        """全プラグインの shutdown() を priority 降順（後発優先）で呼ぶ。

        各プラグインの shutdown() 失敗はログに残して続行（critical でも停止しない）。
        """
        for plugin in reversed(self.plugins):
            try:
                await plugin.shutdown()
                logger.info("plugin shutdown: %s", plugin.name)
            except Exception:
                logger.exception("plugin shutdown failed: %s", plugin.name)

    def has(self, name: str) -> bool:
        """指定された名前のプラグインがロード済みかどうかを返す。"""
        return any(p.name == name for p in self.plugins)

    def get(self, name: str) -> PluginBase | None:
        """指定された名前のプラグインインスタンスを返す。未ロード時は None。"""
        for p in self.plugins:
            if p.name == name:
                return p
        return None

    async def dispatch(self, hook: str, data: Any, ctx=None) -> Any:
        """登録済みプラグインの hook を priority 順に呼び出す。

        critical=False のプラグインは例外をログに残して続行。
        critical=True のプラグインは例外を再raise（チャット中断）。
        """
        for plugin in self.plugins:
            if hook in plugin.hooks:
                try:
                    result = await plugin.run(hook, data, ctx)
                    if result is not None:
                        data = result
                except Exception:
                    logger.exception("%s.%s failed", plugin.name, hook)
                    if plugin.critical:
                        raise
        return data
