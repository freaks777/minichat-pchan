"""プラグインマネージャ。hookディスパッチとプラグイン読み込み。"""

import importlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")

UI_SLOTS = {
    "chat.input_actions",
    "chat.toolbar",
    "studio.actions",
    "settings.plugins",
}
UI_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
UI_DEFINITION_FIELDS = {"slot", "components"}
UI_BUTTON_FIELDS = {"type", "id", "label", "action", "disabled"}
UI_SEPARATOR_FIELDS = {"type", "id"}
UI_STATUS_FIELDS = {"type", "id", "text", "level"}
UI_STATUS_LEVELS = {"info", "success", "warning", "error"}
UI_UPDATE_FIELDS = {"component_id", "text", "level"}
UI_FORM_FIELDS = {"type", "id", "action", "submit_label", "disabled", "fields"}
UI_TEXT_FIELD_FIELDS = {"id", "label", "required", "max_length", "placeholder", "value"}


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

    @staticmethod
    def _validate_ui_definition(plugin: PluginBase, definition) -> dict | None:
        """プラグインUI定義をallowlist方式で検証・正規化する。"""
        if not isinstance(definition, dict):
            return None
        if set(definition) != UI_DEFINITION_FIELDS:
            return None
        slot = definition.get("slot")
        components = definition.get("components")
        if slot not in UI_SLOTS or not isinstance(components, list):
            return None
        if not 1 <= len(components) <= 10:
            return None
        if not UI_NAME_RE.fullmatch(plugin.name or ""):
            return None

        normalized = []
        ids = set()
        for component in components:
            if not isinstance(component, dict):
                return None
            component_id = component.get("id")
            if (
                not isinstance(component_id, str)
                or not UI_NAME_RE.fullmatch(component_id)
                or component_id in ids
            ):
                return None
            ids.add(component_id)
            component_type = component.get("type")
            if component_type == "button":
                if not {"type", "id", "label", "action"}.issubset(component):
                    return None
                if not set(component).issubset(UI_BUTTON_FIELDS):
                    return None
                action = component.get("action")
                label = component.get("label")
                disabled = component.get("disabled", False)
                if isinstance(label, str):
                    label = label.strip()
                if (
                    not isinstance(action, str)
                    or not UI_NAME_RE.fullmatch(action)
                    or not isinstance(label, str)
                    or not 1 <= len(label) <= 80
                    or not isinstance(disabled, bool)
                ):
                    return None
                normalized.append({
                    "type": "button",
                    "id": component_id,
                    "label": label,
                    "action": action,
                    "disabled": disabled,
                })
            elif component_type == "form":
                if not {"type", "id", "action", "submit_label", "fields"}.issubset(component):
                    return None
                if not set(component).issubset(UI_FORM_FIELDS):
                    return None
                action = component.get("action")
                submit_label = component.get("submit_label")
                disabled = component.get("disabled", False)
                fields = component.get("fields")
                if isinstance(submit_label, str):
                    submit_label = submit_label.strip()
                if (
                    not isinstance(action, str)
                    or not UI_NAME_RE.fullmatch(action)
                    or not isinstance(submit_label, str)
                    or not 1 <= len(submit_label) <= 80
                    or not isinstance(disabled, bool)
                    or not isinstance(fields, list)
                    or not 1 <= len(fields) <= 10
                ):
                    return None
                normalized_fields = []
                field_ids = set()
                for field in fields:
                    if not isinstance(field, dict):
                        return None
                    if not {"id", "label", "required", "max_length"}.issubset(field):
                        return None
                    if not set(field).issubset(UI_TEXT_FIELD_FIELDS):
                        return None
                    field_id = field.get("id")
                    label = field.get("label")
                    required = field.get("required")
                    max_length = field.get("max_length")
                    placeholder = field.get("placeholder", "")
                    value = field.get("value", "")
                    if isinstance(label, str):
                        label = label.strip()
                    if (
                        not isinstance(field_id, str)
                        or not UI_NAME_RE.fullmatch(field_id)
                        or field_id in field_ids
                        or not isinstance(label, str)
                        or not 1 <= len(label) <= 80
                        or not isinstance(required, bool)
                        or type(max_length) is not int
                        or not 1 <= max_length <= 2000
                        or not isinstance(placeholder, str)
                        or len(placeholder) > 100
                        or not isinstance(value, str)
                        or len(value) > max_length
                    ):
                        return None
                    field_ids.add(field_id)
                    normalized_fields.append({
                        "id": field_id,
                        "label": label,
                        "required": required,
                        "max_length": max_length,
                        "placeholder": placeholder,
                        "value": value,
                    })
                normalized.append({
                    "type": "form",
                    "id": component_id,
                    "action": action,
                    "submit_label": submit_label,
                    "disabled": disabled,
                    "fields": normalized_fields,
                })
            elif component_type == "separator":
                if set(component) != UI_SEPARATOR_FIELDS:
                    return None
                normalized.append({"type": "separator", "id": component_id})
            elif component_type == "status":
                if set(component) != UI_STATUS_FIELDS:
                    return None
                text = component.get("text")
                level = component.get("level")
                if isinstance(text, str):
                    text = text.strip()
                if (
                    not isinstance(text, str)
                    or not 1 <= len(text) <= 200
                    or level not in UI_STATUS_LEVELS
                ):
                    return None
                normalized.append({
                    "type": "status",
                    "id": component_id,
                    "text": text,
                    "level": level,
                })
            else:
                return None
        return {
            "name": plugin.name,
            "slot": slot,
            "components": normalized,
        }

    @classmethod
    def _validate_ui_definitions(cls, plugin: PluginBase, raw) -> list[dict] | None:
        """Validate one plugin's single or multi-slot definitions atomically."""
        if isinstance(raw, dict):
            raw_definitions = [raw]
        elif isinstance(raw, list) and 1 <= len(raw) <= len(UI_SLOTS):
            raw_definitions = raw
        else:
            return None

        normalized = []
        slots = set()
        component_ids = set()
        button_actions = set()
        form_actions = set()
        component_count = 0
        for raw_definition in raw_definitions:
            definition = cls._validate_ui_definition(plugin, raw_definition)
            if definition is None or definition["slot"] in slots:
                return None
            slots.add(definition["slot"])
            for component in definition["components"]:
                component_id = component["id"]
                if component_id in component_ids:
                    return None
                component_ids.add(component_id)
                if component["type"] == "button":
                    button_actions.add(component["action"])
                elif component["type"] == "form":
                    if component["action"] in form_actions:
                        return None
                    form_actions.add(component["action"])
                component_count += 1
                if component_count > 40:
                    return None
            normalized.append(definition)
        if button_actions & form_actions:
            return None
        return normalized

    def collect_ui_definitions(self) -> list[dict]:
        """Return valid UI definitions in plugin priority and declared slot order."""
        definitions = []
        for plugin in self.plugins:
            try:
                raw = plugin.get_ui_slot()
                if raw is None:
                    continue
                validated = self._validate_ui_definitions(plugin, raw)
                if validated is None:
                    logger.warning("plugin UI definition rejected: %s", plugin.name)
                    continue
                definitions.extend(validated)
            except Exception:
                logger.exception("plugin UI definition failed: %s", plugin.name)
        return definitions

    @staticmethod
    def _normalize_ui_updates(data: dict, status_ids: set[str]) -> dict | None:
        """Validate optional action-driven status updates without partial acceptance."""
        if "ui_updates" not in data:
            return data
        updates = data.get("ui_updates")
        if not isinstance(updates, list) or len(updates) > 10:
            return None

        normalized_updates = []
        seen_ids = set()
        for update in updates:
            if not isinstance(update, dict) or set(update) != UI_UPDATE_FIELDS:
                return None
            component_id = update.get("component_id")
            text = update.get("text")
            level = update.get("level")
            if isinstance(text, str):
                text = text.strip()
            if (
                not isinstance(component_id, str)
                or not UI_NAME_RE.fullmatch(component_id)
                or component_id in seen_ids
                or component_id not in status_ids
                or not isinstance(text, str)
                or not 1 <= len(text) <= 200
                or level not in UI_STATUS_LEVELS
            ):
                return None
            seen_ids.add(component_id)
            normalized_updates.append({
                "component_id": component_id,
                "text": text,
                "level": level,
            })

        normalized_data = dict(data)
        normalized_data["ui_updates"] = normalized_updates
        return normalized_data

    @staticmethod
    def _normalize_ui_form_payload(form: dict, payload: dict) -> dict | None:
        """Validate a submitted form payload against its published field schema."""
        if set(payload) != {"form_id", "values"}:
            return None
        if payload.get("form_id") != form["id"]:
            return None
        values = payload.get("values")
        if not isinstance(values, dict):
            return None
        fields = form["fields"]
        field_ids = {field["id"] for field in fields}
        if set(values) != field_ids:
            return None
        normalized_values = {}
        for field in fields:
            value = values.get(field["id"])
            if (
                not isinstance(value, str)
                or len(value) > field["max_length"]
                or (field["required"] and value == "")
            ):
                return None
            normalized_values[field["id"]] = value
        return {"form_id": form["id"], "values": normalized_values}

    async def dispatch_ui_action(
        self,
        plugin_name: str,
        action: str,
        payload: dict,
        ctx=None,
    ) -> dict:
        """UI定義で公開済みのアクションだけを対象プラグインへ委譲する。"""
        if not UI_NAME_RE.fullmatch(plugin_name or ""):
            raise KeyError("plugin action not found")
        if not UI_NAME_RE.fullmatch(action or ""):
            raise KeyError("plugin action not found")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        definitions = [
            item for item in self.collect_ui_definitions()
            if item["name"] == plugin_name
        ]
        button_actions = {
            component["action"]
            for definition in definitions
            for component in definition["components"]
            if component["type"] == "button" and not component["disabled"]
        }
        forms_by_action = {
            component["action"]: component
            for definition in definitions
            for component in definition["components"]
            if component["type"] == "form" and not component["disabled"]
        }
        if "form_id" in payload:
            form = forms_by_action.get(action)
            if form is None:
                raise KeyError("plugin action not found")
            payload = self._normalize_ui_form_payload(form, payload)
            if payload is None:
                raise ValueError("invalid form payload")
        elif action not in button_actions:
            raise KeyError("plugin action not found")
        status_ids = {
            component["id"]
            for definition in definitions
            for component in definition["components"]
            if component["type"] == "status"
        }

        plugin = self.get(plugin_name)
        if plugin is None:
            raise KeyError("plugin action not found")
        try:
            result = await plugin.handle_ui_action(action, payload, ctx)
        except Exception:
            logger.exception("plugin UI action failed: %s.%s", plugin_name, action)
            return {"status": "error", "message": "plugin action failed", "data": {}}

        if not isinstance(result, dict):
            return {"status": "error", "message": "invalid plugin response", "data": {}}
        status = result.get("status")
        message = result.get("message", "")
        data = result.get("data", {})
        if (
            status not in {"ok", "error"}
            or not isinstance(message, str)
            or len(message) > 500
            or not isinstance(data, dict)
        ):
            return {"status": "error", "message": "invalid plugin response", "data": {}}
        data = self._normalize_ui_updates(data, status_ids)
        if data is None:
            return {"status": "error", "message": "invalid plugin response", "data": {}}
        try:
            serialized_data = json.dumps(data, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return {"status": "error", "message": "invalid plugin response", "data": {}}
        if len(serialized_data.encode("utf-8")) > 65_536:
            return {"status": "error", "message": "invalid plugin response", "data": {}}
        return {"status": status, "message": message, "data": data}

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
