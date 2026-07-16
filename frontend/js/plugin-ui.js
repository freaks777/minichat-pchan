"use strict";

(() => {
  const SLOT_IDS = Object.freeze({
    "chat.input_actions": "plugin-slot-chat-input-actions",
    "chat.toolbar": "plugin-slot-chat-toolbar",
    "studio.actions": "plugin-slot-studio-actions",
    "settings.plugins": "plugin-slot-settings-plugins",
  });
  const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;
  let initGeneration = 0;

  function slots() {
    return Object.values(SLOT_IDS)
      .map(id => document.getElementById(id))
      .filter(Boolean);
  }

  function clearPluginUi() {
    for (const slot of slots()) {
      slot.replaceChildren();
    }
    const feedback = document.getElementById("plugin-ui-feedback");
    if (feedback) {
      feedback.textContent = "";
      feedback.classList.add("is-hidden");
      feedback.classList.remove("is-error");
    }
  }

  function showFeedback(message, isError = false) {
    const feedback = document.getElementById("plugin-ui-feedback");
    if (!feedback) return;
    feedback.textContent = String(message || "");
    feedback.classList.toggle("is-error", Boolean(isError));
    feedback.classList.toggle("is-hidden", !message);
  }

  const STATUS_LEVELS = Object.freeze(["info", "success", "warning", "error"]);
  const STATUS_CLASSES = Object.freeze(
    STATUS_LEVELS.map(level => "plugin-ui-status-" + level)
  );

  function validComponent(component) {
    if (!component || typeof component !== "object" || Array.isArray(component)) return false;
    const keys = Object.keys(component);
    if (typeof component.id !== "string" || !NAME_RE.test(component.id)) return false;
    if (component.type === "button") {
      const allowed = ["type", "id", "label", "action", "disabled"];
      return keys.every(key => allowed.includes(key))
        && typeof component.action === "string"
        && NAME_RE.test(component.action)
        && typeof component.label === "string"
        && component.label.length >= 1
        && component.label.length <= 80
        && typeof component.disabled === "boolean";
    }
    if (component.type === "separator") {
      return keys.length === 2
        && keys.every(key => ["type", "id"].includes(key));
    }
    if (component.type === "status") {
      return keys.length === 4
        && keys.every(key => ["type", "id", "text", "level"].includes(key))
        && typeof component.text === "string"
        && component.text.length >= 1
        && component.text.length <= 200
        && STATUS_LEVELS.includes(component.level);
    }
    return false;
  }

  function normalizeUiUpdates(updates) {
    if (!Array.isArray(updates) || updates.length > 10) return null;
    const normalized = [];
    const ids = new Set();
    for (const update of updates) {
      if (!update || typeof update !== "object" || Array.isArray(update)) return null;
      const keys = Object.keys(update);
      if (keys.length !== 3
          || !keys.every(key => ["component_id", "text", "level"].includes(key))) return null;
      const componentId = update.component_id;
      const text = typeof update.text === "string" ? update.text.trim() : "";
      if (typeof componentId !== "string" || !NAME_RE.test(componentId) || ids.has(componentId)
          || text.length < 1 || text.length > 200 || !STATUS_LEVELS.includes(update.level)) return null;
      ids.add(componentId);
      normalized.push({componentId, text, level: update.level});
    }
    return normalized;
  }

  function applyUiUpdates(pluginName, updates) {
    const normalized = normalizeUiUpdates(updates);
    if (normalized === null) return false;
    const statuses = Array.from(document.querySelectorAll(".plugin-ui-status"));
    for (const update of normalized) {
      const status = statuses.find(element =>
        element.dataset.plugin === pluginName
        && element.dataset.componentId === update.componentId
      );
      if (!status) continue;
      status.textContent = update.text;
      status.classList.remove(...STATUS_CLASSES);
      status.classList.add("plugin-ui-status-" + update.level);
    }
    return true;
  }

  async function runAction(pluginName, component, button) {
    const initiallyDisabled = component.disabled;
    button.disabled = true;
    try {
      const endpoint = "/api/plugins/" + encodeURIComponent(pluginName)
        + "/actions/" + encodeURIComponent(component.action);
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({}),
      });
      const result = await response.json();
      if (!result || typeof result !== "object" || Array.isArray(result)) {
        throw new Error("invalid plugin response");
      }
      const status = result.status;
      const message = typeof result.message === "string" ? result.message : "";
      const data = result.data && typeof result.data === "object" && !Array.isArray(result.data)
        ? result.data
        : {};
      if (!response.ok || !["ok", "error"].includes(status)) {
        throw new Error(message || "plugin action failed");
      }
      if (Object.hasOwn(data, "ui_updates") && !applyUiUpdates(pluginName, data.ui_updates)) {
        throw new Error("invalid plugin UI updates");
      }
      showFeedback(message, status === "error");
      document.dispatchEvent(new CustomEvent("plugin-ui-result", {
        detail: {plugin: pluginName, action: component.action, status, message, data},
      }));
    } catch (error) {
      console.error("plugin UI action failed", error);
      const fallback = typeof t === "function"
        ? t("pluginActionError")
        : "Plugin action failed";
      showFeedback(fallback, true);
    } finally {
      button.disabled = initiallyDisabled;
    }
  }

  function renderDefinition(definition) {
    if (!definition || typeof definition !== "object" || Array.isArray(definition)) return;
    const pluginName = definition.name;
    const slotName = definition.slot;
    const components = definition.components;
    if (!NAME_RE.test(pluginName || "") || !Object.hasOwn(SLOT_IDS, slotName)) return;
    if (!Array.isArray(components) || components.length < 1 || components.length > 10) return;
    if (!components.every(validComponent)) return;

    const slot = document.getElementById(SLOT_IDS[slotName]);
    if (!slot) return;
    const ids = new Set();
    for (const component of components) {
      if (ids.has(component.id)) return;
      ids.add(component.id);
    }
    for (const component of components) {
      if (component.type === "separator") {
        const separator = document.createElement("span");
        separator.className = "plugin-ui-separator";
        separator.dataset.plugin = pluginName;
        separator.dataset.componentId = component.id;
        separator.setAttribute("role", "separator");
        slot.append(separator);
        continue;
      }
      if (component.type === "status") {
        const status = document.createElement("span");
        status.classList.add("plugin-ui-status", "plugin-ui-status-" + component.level);
        status.dataset.plugin = pluginName;
        status.dataset.componentId = component.id;
        status.textContent = component.text;
        slot.append(status);
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "plugin-ui-button";
      button.textContent = component.label;
      button.dataset.plugin = pluginName;
      button.dataset.componentId = component.id;
      button.dataset.action = component.action;
      button.disabled = component.disabled;
      button.addEventListener("click", () => {
        runAction(pluginName, component, button);
      });
      slot.append(button);
    }
  }
  async function initPluginUi() {
    const generation = ++initGeneration;
    clearPluginUi();
    try {
      const response = await fetch("/api/plugins/ui", {cache: "no-store"});
      if (!response.ok) throw new Error("plugin UI HTTP " + response.status);
      const payload = await response.json();
      if (generation !== initGeneration) return;
      if (!payload || payload.version !== 4 || !Array.isArray(payload.plugins)) {
        throw new Error("invalid plugin UI payload");
      }
      for (const definition of payload.plugins) {
        renderDefinition(definition);
      }
    } catch (error) {
      console.error("plugin UI initialization failed", error);
      if (generation === initGeneration) clearPluginUi();
    }
  }

  window.PluginUI = Object.freeze({init: initPluginUi});
  document.addEventListener("DOMContentLoaded", initPluginUi);
})();