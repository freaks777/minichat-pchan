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
    if (component.type === "form") {
      const allowed = ["type", "id", "action", "submit_label", "disabled", "fields"];
      if (keys.length !== 6 || !keys.every(key => allowed.includes(key))
          || typeof component.action !== "string" || !NAME_RE.test(component.action)
          || typeof component.submit_label !== "string"
          || component.submit_label.length < 1 || component.submit_label.length > 80
          || typeof component.disabled !== "boolean"
          || !Array.isArray(component.fields)
          || component.fields.length < 1 || component.fields.length > 10) return false;
      const fieldIds = new Set();
      for (const field of component.fields) {
        if (!field || typeof field !== "object" || Array.isArray(field)) return false;
        const fieldKeys = Object.keys(field);
        if (fieldKeys.length !== 6
            || !fieldKeys.every(key => [
              "id", "label", "required", "max_length", "placeholder", "value",
            ].includes(key))
            || typeof field.id !== "string" || !NAME_RE.test(field.id)
            || fieldIds.has(field.id)
            || typeof field.label !== "string" || field.label.length < 1 || field.label.length > 80
            || typeof field.required !== "boolean"
            || !Number.isInteger(field.max_length)
            || field.max_length < 1 || field.max_length > 2000
            || typeof field.placeholder !== "string" || field.placeholder.length > 100
            || typeof field.value !== "string" || field.value.length > field.max_length) return false;
        fieldIds.add(field.id);
      }
      return true;
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

  async function requestAction(pluginName, action, payload) {
    try {
      const endpoint = "/api/plugins/" + encodeURIComponent(pluginName)
        + "/actions/" + encodeURIComponent(action);
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
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
        detail: {plugin: pluginName, action, status, message, data},
      }));
    } catch (error) {
      console.error("plugin UI action failed", error);
      const fallback = typeof t === "function"
        ? t("pluginActionError")
        : "Plugin action failed";
      showFeedback(fallback, true);
    }
  }

  async function runButtonAction(pluginName, component, button) {
    const initiallyDisabled = component.disabled;
    button.disabled = true;
    try {
      await requestAction(pluginName, component.action, {});
    } finally {
      button.disabled = initiallyDisabled;
    }
  }

  function validDefinition(definition) {
    if (!definition || typeof definition !== "object" || Array.isArray(definition)) return false;
    const keys = Object.keys(definition);
    return keys.length === 3
      && keys.every(key => ["name", "slot", "components"].includes(key))
      && NAME_RE.test(definition.name || "")
      && Object.hasOwn(SLOT_IDS, definition.slot)
      && Array.isArray(definition.components)
      && definition.components.length >= 1
      && definition.components.length <= 10
      && definition.components.every(validComponent);
  }

  function validPluginDefinitions(definitions) {
    if (!Array.isArray(definitions) || definitions.length < 1 || definitions.length > 4) {
      return false;
    }
    const slots = new Set();
    const componentIds = new Set();
    const buttonActions = new Set();
    const formActions = new Set();
    let componentCount = 0;
    for (const definition of definitions) {
      if (!validDefinition(definition) || slots.has(definition.slot)) return false;
      slots.add(definition.slot);
      for (const component of definition.components) {
        if (componentIds.has(component.id)) return false;
        componentIds.add(component.id);
        if (component.type === "button") buttonActions.add(component.action);
        if (component.type === "form") {
          if (formActions.has(component.action)) return false;
          formActions.add(component.action);
        }
        componentCount += 1;
        if (componentCount > 40) return false;
      }
    }
    for (const action of formActions) {
      if (buttonActions.has(action)) return false;
    }
    return true;
  }

  function collectValidDefinitions(definitions) {
    const groups = new Map();
    for (const definition of definitions) {
      if (!definition || typeof definition !== "object" || Array.isArray(definition)) continue;
      const pluginName = definition.name;
      if (typeof pluginName !== "string" || !NAME_RE.test(pluginName)) continue;
      if (!groups.has(pluginName)) groups.set(pluginName, []);
      groups.get(pluginName).push(definition);
    }
    const valid = [];
    for (const definitionsForPlugin of groups.values()) {
      if (validPluginDefinitions(definitionsForPlugin)) {
        valid.push(...definitionsForPlugin);
      }
    }
    return valid;
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
      if (component.type === "form") {
        const form = document.createElement("form");
        form.className = "plugin-ui-form";
        form.dataset.plugin = pluginName;
        form.dataset.componentId = component.id;
        form.dataset.action = component.action;
        const inputs = [];
        for (const field of component.fields) {
          const label = document.createElement("label");
          label.className = "plugin-ui-form-field";
          const labelText = document.createElement("span");
          labelText.textContent = field.label;
          const input = document.createElement("input");
          input.type = "text";
          input.name = field.id;
          input.value = field.value;
          input.placeholder = field.placeholder;
          input.required = field.required;
          input.maxLength = field.max_length;
          input.autocomplete = "off";
          input.disabled = component.disabled;
          label.append(labelText, input);
          form.append(label);
          inputs.push(input);
        }
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "plugin-ui-button";
        submit.textContent = component.submit_label;
        submit.disabled = component.disabled;
        form.append(submit);
        form.addEventListener("submit", async event => {
          event.preventDefault();
          if (component.disabled) return;
          const values = {};
          for (const input of inputs) values[input.name] = input.value;
          const controls = [...inputs, submit];
          const disabledStates = controls.map(control => control.disabled);
          controls.forEach(control => { control.disabled = true; });
          try {
            await requestAction(pluginName, component.action, {
              form_id: component.id,
              values,
            });
          } finally {
            controls.forEach((control, index) => {
              control.disabled = disabledStates[index];
            });
          }
        });
        slot.append(form);
        continue;
      }
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
        runButtonAction(pluginName, component, button);
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
      if (!payload || payload.version !== 6 || !Array.isArray(payload.plugins)) {
        throw new Error("invalid plugin UI payload");
      }
      for (const definition of collectValidDefinitions(payload.plugins)) {
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