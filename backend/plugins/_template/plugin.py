"""Copy this directory and rename TemplatePlugin/name before enabling it."""

from plugins.base import PluginBase


class TemplatePlugin(PluginBase):
    name = "my_plugin"
    hooks = ["on_session_start"]
    priority = 100
    critical = False

    def __init__(self):
        self._state = "Ready"

    async def initialize(self):
        """Open clients or databases here. Keep startup failures explicit."""

    async def shutdown(self):
        """Cancel tasks and close clients or databases here."""

    async def run(self, hook: str, data, ctx):
        if hook == "on_session_start":
            self._state = "Session started"
        return data

    def get_ui_slot(self) -> dict | list[dict] | None:
        return [
            {
                "slot": "chat.toolbar",
                "components": [
                    {
                        "type": "status",
                        "id": "state",
                        "text": self._state,
                        "level": "info",
                    },
                    {
                        "type": "button",
                        "id": "refresh-button",
                        "label": "Refresh",
                        "action": "refresh",
                        "disabled": False,
                    },
                ],
            },
            {
                "slot": "settings.plugins",
                "components": [{
                    "type": "form",
                    "id": "settings-form",
                    "action": "save_settings",
                    "submit_label": "Save",
                    "disabled": False,
                    "fields": [
                        {
                            "id": "display_name",
                            "label": "Display name",
                            "required": True,
                            "max_length": 80,
                            "placeholder": "Plugin name",
                            "value": "",
                        },
                        {
                            "type": "select",
                            "id": "mode",
                            "label": "Mode",
                            "required": True,
                            "options": [
                                {"value": "safe", "label": "Safe"},
                                {"value": "fast", "label": "Fast"},
                            ],
                            "value": "safe",
                        },                        {
                            "type": "checkbox",
                            "id": "enabled",
                            "label": "Enable feature",
                            "required": False,
                            "value": False,
                        },                        {
                            "type": "number",
                            "id": "limit",
                            "label": "Limit",
                            "required": False,
                            "min": 0,
                            "max": 100,
                            "value": None,
                        },
                    ],
                }],
            },
        ]

    async def handle_ui_action(self, action: str, payload: dict, ctx) -> dict:
        if action == "refresh":
            self._state = "Refreshed"
        elif action == "save_settings":
            values = payload.get("values", {})
            display_name = values.get("display_name", "")
            if not display_name.strip():
                return {"status": "error", "message": "Name is required", "data": {}}
            self._state = "Saved"
        else:
            return {"status": "error", "message": "unsupported action", "data": {}}

        return {
            "status": "ok",
            "message": self._state,
            "data": {
                "ui_updates": [{
                    "component_id": "state",
                    "text": self._state,
                    "level": "success",
                }],
            },
        }
