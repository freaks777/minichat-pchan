/* RP Standalone — Persona Studio */

let hasDraft = false;
let _loading = false;
let _fileImportReady = false;
let studioSecrets = [];
let lastStudioSecretTarget = null;

// ── デフォルトID ──
function defaultPersonaId() {
  const dt = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `persona-${dt.getFullYear()}${pad(dt.getMonth()+1)}${pad(dt.getDate())}-${pad(dt.getHours())}${pad(dt.getMinutes())}${pad(dt.getSeconds())}`;
}

// ── スタイル ──
const stylePresets = {
  novel_ai: { viewpoint: "ai_character", person: "first", narration: true },
  novel_user: { viewpoint: "user_character", person: "third", narration: true },
  chat: { viewpoint: "ai_character", person: "first", narration: false },
};

function getStyle() {
  const preset = document.getElementById("t-style-preset").value;
  if (preset !== "custom") return stylePresets[preset];
  return {
    viewpoint: document.getElementById("t-viewpoint").value,
    person: document.getElementById("t-person").value,
    narration: document.getElementById("t-narration").value === "true",
  };
}

function onStyleChange() {
  document.getElementById("t-style-custom").style.display =
    document.getElementById("t-style-preset").value === "custom" ? "inline-flex" : "none";
}

// ── UI補助 ──
let _studioAbortController = null;

function setLoading(active, msg) {
  _loading = active;
  document.getElementById("loading-overlay").style.display = active ? "flex" : "none";
  if (msg) document.querySelector("#loading-overlay span").textContent = msg;
  document.querySelectorAll(".btn-primary").forEach(b => {
    if (b.id !== "lang-toggle") b.disabled = active;
  });
  const importButton = document.getElementById("studio-import");
  if (importButton && !active) importButton.disabled = !_fileImportReady;
}

function getStudioAbortController() {
  _studioAbortController = new AbortController();
  return _studioAbortController;
}

async function cancelStudioOp() {
  if (_studioAbortController) _studioAbortController.abort();
  try { await fetch("/api/persona-studio/cancel", { method: "POST" }); } catch (_) {}
  setLoading(false);
}


// ── 機密項目 ──

function studioSecretData() {
  return studioSecrets.map(item => ({ label: item.label || "", placeholder: item.placeholder }));
}

function insertStudioPlaceholder(placeholder) {
  const target = lastStudioSecretTarget;
  if (!target || !document.contains(target) || !("setRangeText" in target)) {
    setStatus(t("studioSecretTargetRequired"), true);
    return false;
  }
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? start;
  target.setRangeText(placeholder, start, end, "end");
  target.dispatchEvent(new Event("input", { bubbles: true }));
  target.focus();
  return true;
}

function renderStudioSecrets() {
  const list = document.getElementById("studio-secrets-list");
  if (!list) return;
  list.textContent = "";
  studioSecrets.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "studio-secret-row";
    const label = document.createElement("span");
    label.className = "studio-secret-label";
    label.textContent = item.label || t("studioSecretUnnamed");
    const masked = document.createElement("span");
    masked.className = "secret-masked";
    masked.textContent = "●●●●●";
    const reveal = document.createElement("button");
    reveal.type = "button";
    reveal.className = "btn btn-secondary btn-sm";
    reveal.textContent = "👁";
    reveal.addEventListener("click", async () => {
      if (row.classList.contains("revealed")) {
        masked.textContent = "●●●●●";
        row.classList.remove("revealed");
        return;
      }
      const res = await fetch("/api/secrets/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ placeholder: item.placeholder }),
      });
      const data = await res.json();
      if (res.ok) {
        masked.textContent = data.value;
        row.classList.add("revealed");
      } else {
        masked.textContent = t("secretUnavailable");
      }
    });
    const insert = document.createElement("button");
    insert.type = "button";
    insert.className = "btn btn-secondary btn-sm";
    insert.textContent = t("secretInsert");
    insert.addEventListener("click", () => insertStudioPlaceholder(item.placeholder));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-secondary btn-sm";
    remove.textContent = "×";
    remove.title = t("studioSecretRemove");
    remove.addEventListener("click", () => {
      studioSecrets.splice(index, 1);
      renderStudioSecrets();
    });
    row.append(label, masked, reveal, insert, remove);
    list.appendChild(row);
  });
}

function secretsFromText(text) {
  return [...new Set(String(text || "").match(/\{\{secret:\d+\}\}/g) || [])]
    .map(placeholder => ({ label: "", placeholder }));
}

function restoreStudioSecrets(items) {
  studioSecrets = Array.isArray(items)
    ? items.filter(item => item && /^\{\{secret:\d+\}\}$/.test(item.placeholder || ""))
        .map(item => ({ label: String(item.label || ""), placeholder: item.placeholder }))
    : [];
  renderStudioSecrets();
}

// ── ドラフト保存/読込 ──

async function saveFormDraft() {
  const personaId = document.getElementById("t-persona-id").value.trim();
  if (!personaId) { setStatus("ペルソナIDを入力してください", true); return; }

  // 上書き確認: 既存の下書きと名前が異なる場合は確認
  try {
    const check = await fetch("/api/persona-studio/load-draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId }),
    });
    const cr = await check.json();
    if (cr.status === "ok" && cr.data) {
      const oldName = (cr.data.fields || {}).name || "";
      const newName = document.getElementById("t-name").value.trim();
      if (oldName && newName && oldName !== newName) {
        if (!confirm(`下書き「${oldName}」を「${newName}」で上書きしますか？`)) return;
      }
    }
  } catch (_) { /* チェック失敗時はそのまま保存 */ }

  const data = {
    persona_id: personaId,
    fields: {},
    extra_sections: getExtraSections(),
    secrets: studioSecretData(),
  };
  ALL_T_FIELDS.forEach(id => {
    const el = document.getElementById("t-" + id);
    if (el) data.fields[id] = el.value;
  });

  try {
    const res = await fetch("/api/persona-studio/save-draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId, data }),
    });
    const r = await res.json();
    if (r.error) { setStatus(r.error, true); return; }
    if (r.existing_persona) {
      showToast("⚠ 既存のペルソナと同じIDです。生成・保存時に上書きされます");
    } else {
      showToast("✓ 下書きを保存しました");
    }
  } catch (err) {
    setStatus("保存失敗: " + err, true);
  }
}

async function loadFormDraft(personaId) {
  try {
    const res = await fetch("/api/persona-studio/load-draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId }),
    });
    const r = await res.json();
    if (r.status === "not_found" || r.error) return false;

    const d = r.data;
    // persona_id を復元（ALL_T_FIELDS に含まれないため明示的）
    if (d.persona_id) {
      document.getElementById("t-persona-id").value = d.persona_id;
      document.getElementById("d-persona-id").value = d.persona_id;
    }
    if (d.fields) {
      Object.entries(d.fields).forEach(([k, v]) => {
        const el = document.getElementById("t-" + k);
        if (el) el.value = v || "";
      });
    }
    if (d.extra_sections) setExtraSections(d.extra_sections);
    restoreStudioSecrets(d.secrets || []);
    // 本データの表示状態をクリア（下書きは生成前の状態）
    document.getElementById("result-panel").style.display = "none";
    document.getElementById("action-bar").style.display = "none";
    hasDraft = false;
    showToast("✓ 下書きを読み込みました");
    return true;
  } catch (err) {
    console.error("loadDraft:", err);
    return false;
  }
}

function setStatus(msg, isError) {
  const bar = document.getElementById("status-bar");
  bar.textContent = msg;
  bar.style.color = isError ? "var(--error)" : "var(--text-dim)";
}

function resetForm(prefix) {
  ALL_T_FIELDS.forEach(id => {
    const el = document.getElementById(prefix + "-" + id);
    if (el) el.value = "";
  });
  if (prefix === "t") {
    document.getElementById("raw-text").value = "";
    setExtraSections([]);
    restoreStudioSecrets([]);
  }
}

function showToast(msg, isError) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.style.background = isError ? "var(--error)" : "var(--accent)";
  toast.style.display = "block";
  setTimeout(() => { toast.style.display = "none"; }, 3000);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── タブ切替 ──
function switchTab(id) {
  document.querySelectorAll(".tab-row button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  document.querySelector(`.tab-row button[data-studio-tab="${id}"]`).classList.add("active");
  document.getElementById("tab-" + id).classList.add("active");
  if (id === "saved") loadSavedPersonas();
}

// ── 全フォームフィールドID ──
const ALL_T_FIELDS = [
  "name","sex","gender","age","birthday","species","blood",
  "height","weight","bwh","hair","eyes","skin","clothing",
  "personality","principles","firstperson","secondperson","tone","speech",
  "likes","habits","occupation","skills",
  "background","forbidden","opening_scene"
];

function resetForm(prefix) {
  ALL_T_FIELDS.forEach(id => {
    const el = document.getElementById(prefix + "-" + id);
    if (el) el.value = "";
  });
  if (prefix === "t") {
    document.getElementById("raw-text").value = "";
    setExtraSections([]);
    restoreStudioSecrets([]);
  }
}

function resetAll() {
  const hasInput = hasDraft || ALL_T_FIELDS.some(id => {
    const el = document.getElementById("t-" + id);
    return el && el.value.trim();
  }) || (document.getElementById("raw-text").value.trim())
    || (document.getElementById("d-source-dir").value.trim());
  if (hasInput && !confirm("入力内容をすべてリセットしますか？")) return;

  ALL_T_FIELDS.forEach(id => {
    const el = document.getElementById("t-" + id);
    if (el) el.value = "";
  });
  document.getElementById("raw-text").value = "";
  document.getElementById("raw-text").dispatchEvent(new Event("input"));
  document.getElementById("d-source-dir").value = "";
  setFileImportReady(false);
  document.getElementById("t-persona-id").value = defaultPersonaId();
  document.getElementById("d-persona-id").value = document.getElementById("t-persona-id").value;
  validatePersonaId(document.getElementById("t-persona-id"));
  validatePersonaId(document.getElementById("d-persona-id"));
  document.getElementById("t-style-preset").value = "novel_ai";
  document.getElementById("t-style-custom").style.display = "none";
  document.getElementById("file-validation").style.display = "none";
  setExtraSections([]);
  document.getElementById("result-panel").style.display = "none";
  document.getElementById("action-bar").style.display = "none";
  hasDraft = false;
  setStatus(t("statusReady"));
}

// ── 結果表示 ──
function showResult(draft) {
  hasDraft = true;
  document.getElementById("result-soul").value = draft.soul_md || "";
  document.getElementById("result-skill").value = draft.skill_md || "";
  document.getElementById("result-panel").style.display = "block";
  document.getElementById("action-bar").style.display = "flex";
  switchResultTab("soul");
}

function switchResultTab(tab) {
  document.getElementById("result-soul").style.display = tab === "soul" ? "block" : "none";
  document.getElementById("result-skill").style.display = tab === "skill" ? "block" : "none";
  document.getElementById("result-tab-soul").className = "btn btn-secondary btn-sm" + (tab === "soul" ? " active" : "");
  document.getElementById("result-tab-skill").className = "btn btn-secondary btn-sm" + (tab === "skill" ? " active" : "");
}

function toggleTestChat() {
  const o = document.getElementById("test-overlay");
  o.style.display = o.style.display === "flex" ? "none" : "flex";
}

// ── フィールド抽出（v3.3: LLMで構造化JSON抽出 → フォーム反映） ──

// ── 自由設定（extra_sections）DOM管理 ──

function addExtraSection(title, content) {
  title = title || "";
  content = content || "";
  const container = document.getElementById("extra-sections-list");
  const div = document.createElement("div");
  const titleInput = document.createElement("input");
  const contentInput = document.createElement("textarea");
  const removeButton = document.createElement("button");
  div.className = "extra-section-item";
  titleInput.className = "extra-title";
  titleInput.value = title;
  titleInput.placeholder = "タイトル（任意）";
  contentInput.className = "extra-content";
  contentInput.value = content;
  contentInput.placeholder = "内容";
  removeButton.type = "button";
  removeButton.className = "btn btn-danger btn-sm extra-section-remove";
  removeButton.textContent = "× 削除";
  removeButton.addEventListener("click", () => div.remove());
  div.append(titleInput, contentInput, removeButton);
  container.appendChild(div);
}
function removeExtraSection(btn) {
  btn.closest(".extra-section-item").remove();
}

function getExtraSections() {
  const items = document.querySelectorAll(".extra-section-item");
  const result = [];
  items.forEach(item => {
    const title = item.querySelector(".extra-title").value.trim();
    const content = item.querySelector(".extra-content").value.trim();
    if (content) result.push({ title, content });
  });
  return result;
}

function setExtraSections(data) {
  const container = document.getElementById("extra-sections-list");
  container.replaceChildren();
  if (Array.isArray(data)) {
    data.forEach(s => addExtraSection(s.title || "", s.content || ""));
  }
}

// ── フォーム反映（JSON → フォーム） ──

function fillFormFromFields(fields) {
  // JSONキー → フォームID の直接マッピング。正規表現廃止。
  const mapping = {
    name: "t-name", sex: "t-sex", gender: "t-gender", age: "t-age",
    birthday: "t-birthday", species: "t-species", blood: "t-blood",
    height: "t-height", weight: "t-weight", bwh: "t-bwh",
    hair: "t-hair", eyes: "t-eyes", skin: "t-skin", clothing: "t-clothing",
    personality: "t-personality", principles: "t-principles",
    firstperson: "t-firstperson", secondperson: "t-secondperson",
    tone: "t-tone", speech: "t-speech",
    likes: "t-likes", habits: "t-habits",
    occupation: "t-occupation", skills: "t-skills",
    background: "t-background", forbidden: "t-forbidden",
    opening_scene: "t-opening_scene",
  };
  for (const [key, elId] of Object.entries(mapping)) {
    if (fields[key] !== undefined && fields[key] !== null) {
      const el = document.getElementById(elId);
      if (el) el.value = String(fields[key]);
    }
  }
}

async function extractFields() {
  if (_loading) return;
  const text = document.getElementById("raw-text").value.trim();
  if (!text) { setStatus(t("statusNeedText"), true); return; }
  const personaId = document.getElementById("t-persona-id").value.trim() || defaultPersonaId();
  setLoading(true, "抽出中...（モデルにより1〜5分かかります）");
  try {
    const controller = getStudioAbortController();
    const res = await fetch("/api/persona-studio/extract-fields", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, persona_id: personaId }),
      signal: controller.signal,
    });
    const data = await res.json();
    if (data.error) { setLoading(false); setStatus(data.error, true); return; }

    fillFormFromFields(data.fields || {});
    setExtraSections(data.extra_sections || []);
    setLoading(false);
    const extraCount = (data.extra_sections || []).length;
    setStatus(`抽出完了: ${Object.keys(data.fields || {}).filter(k => data.fields[k]).length} 項目反映` + (extraCount > 0 ? ` / extra_sections: ${extraCount}件` : ""));
    showToast("✓ フィールド抽出完了。必要に応じて編集し「フォームから生成」を押してください");
  } catch (err) {
    setLoading(false);
    setStatus(err.name === "AbortError" ? "タイムアウト（900秒）: API応答なし" : "通信エラー: " + err, true);
  }
}

// ── 旧: テキスト→SOUL.md直接変換（非推奨、後方互換） ──

function readTemplateFields() {
  const f = {};
  ALL_T_FIELDS.forEach(id => { f[id] = document.getElementById("t-" + id).value; });
  return f;
}

async function generateFromTemplate() {
  if (_loading) return;
  const personaId = document.getElementById("t-persona-id").value.trim();
  const name = document.getElementById("t-name").value.trim();
  if (!personaId) { setStatus("ペルソナIDは必須です", true); return; }
  if (!name) { setStatus("名前は必須です", true); return; }
  setLoading(true, "生成中...（モデルにより1〜3分かかります）");
  try {
    const res = await fetch("/api/persona-studio/create-template", {
      method: "POST", headers: { "Content-Type": "application/json" },
      signal: getStudioAbortController().signal,
      body: JSON.stringify({
        persona_id: personaId,
        fields: readTemplateFields(),
        extra_sections: getExtraSections(),
    secrets: studioSecretData(),
        style_override: getStyle(),
      }),
    });
    const data = await res.json();
    if (data.error) { setLoading(false); setStatus(data.error, true); return; }
    showResult(data.draft);
    setLoading(false);
    setStatus(t("statusReady"));
  } catch (err) { setLoading(false); setStatus("通信エラー: " + err, true); }
}

// ── 保存 ──
async function saveDraft() {
  if (!hasDraft) { setStatus("先に生成してください", true); return; }

  let personaId = document.getElementById("t-persona-id").value.trim()
               || document.getElementById("d-persona-id").value.trim();
  if (!personaId) {
    personaId = defaultPersonaId();
    document.getElementById("t-persona-id").value = personaId;
    document.getElementById("d-persona-id").value = personaId;
  }

  try {
    const listRes = await fetch("/api/persona/list");
    const personas = await listRes.json();
    if (personas.some(p => p.id === personaId)) {
      if (!confirm(`「${personaId}」は既に存在します。上書き保存しますか？`)) return;
    }
  } catch (err) { /* 照合失敗時は確認なしで続行 */ }

  const draft = {
    persona_id: personaId,
    soul_md: document.getElementById("result-soul").value,
    skill_md: document.getElementById("result-skill").value,
    extra_sections: getExtraSections(),
    secrets: studioSecretData(),
    style: getStyle(),
  };

  setStatus("保存中...");
  try {
    const res = await fetch("/api/persona-studio/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId, draft }),
    });
    const data = await res.json();
    if (data.error) { setStatus(data.error, true); showToast(data.error, true); return; }
    const warningCode = data.warning && data.warning.code ? String(data.warning.code) : "";
    setStatus("保存しました: " + personaId + (warningCode ? " (索引警告: " + warningCode + ")" : ""), Boolean(warningCode));
    showToast("✓ 保存完了: " + personaId + (warningCode ? " / 索引は再構築できます" : ""), Boolean(warningCode));
    hasDraft = true;
    document.getElementById("result-panel").style.display = "block";
    document.getElementById("action-bar").style.display = "flex";
    await loadSavedPersonas();
  } catch (err) { setStatus("通信エラー: " + err, true); showToast("保存失敗: " + err, true); }
}

// ── テスト会話 ──
async function doTestChat() {
  if (!hasDraft) { setStatus("先に生成してください", true); return; }
  const msg = document.getElementById("test-msg").value.trim();
  if (!msg) return;
  setLoading(true, "応答生成中...");
  try {
    const draft = {
      persona_id: document.getElementById("t-persona-id").value.trim(),
      soul_md: document.getElementById("result-soul").value,
      skill_md: document.getElementById("result-skill").value,
      style: getStyle(),
    };
    const res = await fetch("/api/persona-studio/test-chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft, message: msg }),
    });
    const data = await res.json();
    if (data.error) { setLoading(false); setStatus(data.error, true); showToast(data.error, true); return; }
    setLoading(false);
    const log = document.getElementById("test-log");
    log.textContent += (log.textContent ? "\n\n" : "") + "👤 " + msg + "\n🤖 " + data.response;
    document.getElementById("test-msg").value = "";
    document.getElementById("test-msg").style.height = "auto";
    document.getElementById("test-overlay").style.display = "flex";
    setStatus(t("statusReady"));
  } catch (err) { setLoading(false); setStatus("通信エラー: " + err, true); }
}

// ── インポート ──
function setFileImportReady(ready) {
  _fileImportReady = Boolean(ready);
  const button = document.getElementById("studio-import");
  if (button) button.disabled = _loading || !_fileImportReady;
}

function appendFileValidationLine(container, className, text) {
  const line = document.createElement("span");
  line.className = className;
  line.textContent = text;
  container.append(line, document.createElement("br"));
}

async function validateFiles() {
  const sourceDir = document.getElementById("d-source-dir").value.trim();
  setFileImportReady(false);
  if (!sourceDir) { setStatus(t("importSourceRequired"), true); return; }
  setStatus(t("importChecking"));
  try {
    const res = await fetch("/api/persona-studio/validate-files", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_dir: sourceDir }),
    });
    const data = await res.json();
    const el = document.getElementById("file-validation");
    el.style.display = "block";
    el.replaceChildren();
    if (!res.ok || data.error) {
      appendFileValidationLine(el, "validation-error", t("importValidationFailed") + ": " + String(data.error || res.status));
      if (el.lastElementChild && el.lastElementChild.tagName === "BR") el.lastElementChild.remove();
      setStatus(t("importValidationFailed"), true);
      return;
    }

    const found = Array.isArray(data.found) ? data.found : [];
    const missing = Array.isArray(data.missing) ? data.missing : [];
    const invalid = Array.isArray(data.invalid) ? data.invalid : [];
    found.forEach(file => appendFileValidationLine(el, "validation-found", "✓ " + String(file)));
    missing.forEach(file => appendFileValidationLine(el, "validation-missing", "⚠ " + String(file) + " — " + t("importFileRequired")));
    invalid.forEach(file => appendFileValidationLine(el, "validation-error", "✕ " + String(file) + " — " + t("importFileInvalid")));
    if (el.lastElementChild && el.lastElementChild.tagName === "BR") el.lastElementChild.remove();

    const complete = data.status === "complete" && missing.length === 0 && invalid.length === 0;
    setFileImportReady(complete);
    setStatus(complete ? t("importCompleteReady") : t("importNeedsComplete"), !complete);
  } catch (err) {
    setFileImportReady(false);
    setStatus(t("importValidationFailed") + ": " + err, true);
  }
}

async function importPersona() {
  if (_loading) return;
  const personaId = document.getElementById("d-persona-id").value.trim();
  const sourceDir = document.getElementById("d-source-dir").value.trim();
  if (!personaId) { setStatus(t("importPersonaIdRequired"), true); return; }
  if (!sourceDir) { setStatus(t("importSourceRequired"), true); return; }
  if (!_fileImportReady) { setStatus(t("importNeedsValidation"), true); return; }

  setLoading(true, t("importInProgress"));
  try {
    const res = await fetch("/api/persona-studio/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId, source_dir: sourceDir }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      let message = t("importFailed");
      if (data.error === "incomplete_persona") message = t("importNeedsComplete");
      if (data.error === "invalid_persona_file") message = t("importFileInvalid");
      if (data.error === "persona_exists") message = t("importPersonaExists");
      setStatus(message + " (" + String(data.error || res.status) + ")", true);
      showToast(message, true);
      return;
    }

    const warningCode = data.warning && data.warning.code ? String(data.warning.code) : "";
    _fileImportReady = false;
    setStatus(t("importDone") + ": " + personaId + (warningCode ? " (" + t("importIndexWarning") + ": " + warningCode + ")" : ""), Boolean(warningCode));
    showToast("✓ " + t("importDone") + ": " + personaId, Boolean(warningCode));
    await loadSavedPersonas();
  } catch (err) {
    setStatus(t("importFailed") + ": " + err, true);
    showToast(t("importFailed"), true);
  } finally {
    setLoading(false);
  }
}

// ── 保存済み一覧 ──
async function loadSavedPersonas() {
  const container = document.getElementById("saved-persona-list");
  const loading = document.createElement("span");
  loading.className = "text-muted";
  loading.textContent = "読み込み中...";
  container.replaceChildren(loading);
  try {
    const res = await fetch("/api/persona/list");
    const personas = await res.json();
    container.replaceChildren();
    if (!personas.length) {
      const empty = document.createElement("span");
      empty.className = "text-muted";
      empty.textContent = "登録済みペルソナはありません";
      container.appendChild(empty);
      return;
    }

    personas.forEach(p => {
      const personaId = String(p.id ?? "");
      const isDraftOnly = p.status === "draft_only";
      const hasDraft = isDraftOnly || Boolean(p.has_draft);
      const loadPersona = () => isDraftOnly ? loadFormDraft(personaId) : loadDraft(personaId);
      const card = document.createElement("div");
      const main = document.createElement("div");
      const meta = document.createElement("span");
      const date = document.createElement("span");
      const id = document.createElement("span");
      const name = document.createElement("span");
      const actions = document.createElement("div");
      const loadButton = document.createElement("button");
      const deleteButton = document.createElement("button");

      card.className = "saved-persona-item" + (hasDraft ? " has-draft" : "");
      card.addEventListener("click", loadPersona);
      card.addEventListener("dblclick", () => deletePersona(personaId));
      main.className = "saved-persona-main";
      meta.className = "saved-persona-meta";
      date.className = "saved-persona-date";
      date.textContent = String(p.updated ?? "");
      id.className = "saved-persona-id";
      id.textContent = personaId;
      meta.append(date, id);
      if (hasDraft) {
        const badge = document.createElement("span");
        badge.className = "draft-badge";
        badge.textContent = isDraftOnly ? "下書きのみ" : "下書きあり";
        meta.appendChild(badge);
      }
      name.className = "saved-persona-name";
      name.textContent = String(p.name ?? "");
      main.append(meta, name);

      actions.className = "saved-persona-actions";
      loadButton.className = "btn btn-secondary btn-sm";
      loadButton.textContent = isDraftOnly ? "下書き読込" : "読込";
      loadButton.addEventListener("click", event => {
        event.stopPropagation();
        loadPersona();
      });
      deleteButton.className = "btn btn-danger btn-sm";
      deleteButton.textContent = "削除";
      deleteButton.addEventListener("click", event => {
        event.stopPropagation();
        deletePersona(personaId);
      });
      actions.append(loadButton, deleteButton);
      card.append(main, actions);
      container.appendChild(card);
    });
  } catch (err) {
    const error = document.createElement("span");
    error.className = "validation-error";
    error.textContent = "読み込み失敗";
    container.replaceChildren(error);
  }
}
// ── SOUL.md → フォーム抽出 ──
function setMatch(text, regex, fieldId) {
  const m = text.match(regex);
  if (m) document.getElementById(fieldId).value = m[1].trim();
}

function extractSection(text, heading) {
  const re = new RegExp(`##\\s*[■□]?\\s*${heading}[\\s\\S]*?(?=\\n##\\s|\\n---|$)`, "i");
  const m = text.match(re);
  return m ? m[0].replace(/^##.*\n/, "").trim() : "";
}

function fillTemplateForm(soul) {
  if (!soul) return;

  const nameMatch = soul.match(/#\s*SOUL:\s*(.+?)(?:\s*[—–-].*)?$/m);
  if (nameMatch) document.getElementById("t-name").value = nameMatch[1].trim();

  const allText = soul;
  setMatch(allText, /身体的性別[：:]\s*(.+)/, "t-sex");
  setMatch(allText, /^\s*-\s*\*\*性別\*\*[：:]\s*(.+)/m, "t-sex");
  setMatch(allText, /性自認[：:]\s*(.+)/, "t-gender");
  setMatch(allText, /年齢[：:]\s*(.+)/, "t-age");
  setMatch(allText, /^\s*-\s*\*\*年齢\*\*[：:]\s*(.+)/m, "t-age");
  setMatch(allText, /誕生日[：:]\s*(.+)/, "t-birthday");
  setMatch(allText, /種族[：:]\s*(.+)/, "t-species");
  setMatch(allText, /血液型[：:]\s*(.+)/, "t-blood");
  setMatch(allText, /身長[：:]\s*(.+)/, "t-height");
  setMatch(allText, /^\s*-\s*\*\*体格\*\*[：:]\s*(.+)/m, "t-height");
  setMatch(allText, /体重[：:]\s*(.+)/, "t-weight");
  setMatch(allText, /BWH[：:]\s*(.+)/, "t-bwh");
  setMatch(allText, /^\s*-\s*\*\*髪\*\*[：:]\s*(.+)/m, "t-hair");
  setMatch(allText, /髪[：:]\s*(.+)/, "t-hair");
  setMatch(allText, /^\s*-\s*\*\*目\*\*[：:]\s*(.+)/m, "t-eyes");
  setMatch(allText, /目[：:]\s*(.+)/, "t-eyes");
  setMatch(allText, /^\s*-\s*\*\*肌\*\*[：:]\s*(.+)/m, "t-skin");
  setMatch(allText, /肌[：:]\s*(.+)/, "t-skin");
  setMatch(allText, /^\s*-\s*\*\*服装\*\*[：:]\s*(.+)/m, "t-clothing");
  setMatch(allText, /服装[：:]\s*(.+)/, "t-clothing");
  setMatch(allText, /一人称[：:]\s*["「]?(.+?)["」]?\s*$/m, "t-firstperson");
  setMatch(allText, /^\s*-\s*\*\*一人称\*\*[：:]\s*["「]?(.+?)["」]?\s*$/m, "t-firstperson");
  setMatch(allText, /二人称[：:]\s*["「]?(.+?)["」]?\s*$/m, "t-secondperson");
  setMatch(allText, /^\s*-\s*\*\*二人称\*\*[：:]\s*["「]?(.+?)["」]?\s*$/m, "t-secondperson");
  setMatch(allText, /職業[：:]\s*(.+)/, "t-occupation");
  setMatch(allText, /^\s*-\s*\*\*職業\*\*[：:]\s*(.+)/m, "t-occupation");
  setMatch(allText, /所属[：:]\s*(.+)/, "t-occupation");
  setMatch(allText, /能力[：:]\s*(.+)/, "t-skills");
  setMatch(allText, /スキル[：:]\s*(.+)/, "t-skills");

  document.getElementById("t-personality").value = extractSection(allText, "人格定義");
  document.getElementById("t-principles").value = extractSection(allText, "行動原理");
  document.getElementById("t-tone").value = extractSection(allText, "口調[^サ]") || extractSection(allText, "口調の特徴");
  document.getElementById("t-speech").value = extractSection(allText, "口調サンプル") || extractSection(allText, "セリフサンプル");
  document.getElementById("t-likes").value = extractSection(allText, "好き嫌い");
  document.getElementById("t-habits").value = extractSection(allText, "癖");
  document.getElementById("t-background").value = extractSection(allText, "背景");
  document.getElementById("t-forbidden").value = extractSection(allText, "禁止事項");
  document.getElementById("t-opening_scene").value = extractSection(allText, "開始時の状況");
}

async function loadDraft(personaId) {
  setStatus("読み込み中...");
  try {
    const res = await fetch("/api/persona-studio/load/" + encodeURIComponent(personaId));
    const data = await res.json();
    if (data.error) { setStatus(data.error, true); showToast(data.error, true); return; }
    const d = data.draft;

    document.getElementById("t-persona-id").value = d.persona_id || "";
    document.getElementById("d-persona-id").value = d.persona_id || "";
    validatePersonaId(document.getElementById("t-persona-id"));
    validatePersonaId(document.getElementById("d-persona-id"));

    if (d.style) {
      const s = d.style;
      document.getElementById("t-viewpoint").value = s.viewpoint || "ai_character";
      document.getElementById("t-person").value = s.person || "first";
      document.getElementById("t-narration").value = s.narration ? "true" : "false";
      const match = Object.entries(stylePresets).find(([,v]) =>
        v.viewpoint === s.viewpoint && v.person === s.person && v.narration === s.narration);
      document.getElementById("t-style-preset").value = match ? match[0] : "custom";
      onStyleChange();
    }

    fillTemplateForm(d.soul_md || "");
    setExtraSections(d.extra_sections || []);
    restoreStudioSecrets(d.secrets || secretsFromText((d.soul_md || "") + "\n" + (d.skill_md || "")));
    showResult(d);
    setStatus("読み込み完了: " + personaId);
    showToast("✓ 読み込み: " + personaId);
  } catch (err) { setStatus("読込失敗: " + err, true); }
}

async function deletePersona(personaId) {
  try {
    const previewRes = await fetch("/api/persona-studio/delete/" + encodeURIComponent(personaId) + "/preview");
    const preview = await previewRes.json();
    if (preview.error) { setStatus(preview.error, true); showToast(preview.error, true); return; }
    if (preview.active) {
      const message = "使用中のペルソナは削除できません。先に別のペルソナへ切り替えてください。";
      setStatus(message, true); showToast(message, true); return;
    }
    const resources = preview.resources || {};
    const count = Object.values(resources).reduce((sum, value) => sum + Number(value || 0), 0);
    if (!confirm("ペルソナ '" + personaId + "' と関連データ " + count + " 件を削除しますか？")) return;
    setStatus("削除中...");
    const res = await fetch("/api/persona-studio/delete/" + encodeURIComponent(personaId), { method: "DELETE" });
    const data = await res.json();
    if (data.error) { setStatus(data.error, true); showToast(data.error, true); return; }
    if (data.status === "partial" || data.status === "error") {
      const failed = (data.failed_resources || []).join(", ") || "unknown";
      const message = "一部の削除に失敗しました (" + failed + ")。再試行してください。";
      setStatus(message, true); showToast(message, true); return;
    }
    setStatus("削除しました: " + personaId);
    showToast("✓ 削除: " + personaId);
    await loadSavedPersonas();
  } catch (err) { setStatus("削除失敗: " + err, true); showToast("削除失敗: " + err, true); }
}

// ── バリデーション ──
const PERSONA_ID_RE = /^[a-zA-Z0-9_-]*$/;

function validatePersonaId(el) {
  const hint = el.parentElement.querySelector(".validation-hint");
  if (!hint) return;
  const valid = PERSONA_ID_RE.test(el.value);
  el.classList.toggle("invalid", !valid && el.value.length > 0);
  hint.textContent = valid || el.value.length === 0 ? "" : "半角英数字・ハイフン・アンダースコアのみ使用可";
  hint.classList.toggle("visible", !valid && el.value.length > 0);
}

function syncPersonaIdAndValidate(fromId, toId) {
  const fromEl = document.getElementById(fromId);
  const toEl = document.getElementById(toId);
  if (fromEl && toEl) {
    toEl.value = fromEl.value;
    validatePersonaId(fromEl);
    validatePersonaId(toEl);
  }
}

// ── 初期化 ──
document.addEventListener("DOMContentLoaded", () => {
  i18nApply();
  updateLangToggle();
  document.querySelectorAll('[data-studio-tab]').forEach(button => {
    button.addEventListener('click', () => switchTab(button.dataset.studioTab));
  });
  document.getElementById('studio-reset-all').addEventListener('click', resetAll);
  document.getElementById('studio-toggle-test').addEventListener('click', toggleTestChat);
  document.getElementById('studio-save-draft').addEventListener('click', saveDraft);
  document.getElementById('t-style-preset').addEventListener('change', onStyleChange);
  document.getElementById('studio-extract-fields').addEventListener('click', extractFields);
  document.getElementById('studio-add-section').addEventListener('click', () => addExtraSection());
  document.getElementById('studio-generate').addEventListener('click', generateFromTemplate);
  document.getElementById('studio-reset-form').addEventListener('click', () => resetForm('t'));
  document.getElementById('studio-save-form-draft').addEventListener('click', saveFormDraft);
  document.getElementById('studio-validate-files').addEventListener('click', validateFiles);
  document.getElementById('studio-import').addEventListener('click', importPersona);
  document.getElementById('d-source-dir').addEventListener('input', () => {
    setFileImportReady(false);
    document.getElementById('file-validation').style.display = 'none';
  });
  document.getElementById('result-tab-soul').addEventListener('click', () => switchResultTab('soul'));
  document.getElementById('result-tab-skill').addEventListener('click', () => switchResultTab('skill'));
  document.getElementById('studio-close-test').addEventListener('click', toggleTestChat);
  document.getElementById('studio-send-test').addEventListener('click', doTestChat);
  document.getElementById('studio-cancel-op').addEventListener('click', cancelStudioOp);

  const defaultId = defaultPersonaId();
  document.getElementById("t-persona-id").value = defaultId;
  document.getElementById("d-persona-id").value = defaultId;

  const secretArea = document.getElementById("studio-secrets-area");
  const secretDialog = document.getElementById("studio-secret-dialog");
  const secretForm = document.getElementById("studio-secret-form");
  document.addEventListener("focusin", (event) => {
    const target = event.target;
    if (target.matches("#studio-ui input, #studio-ui textarea")
        && !target.closest("#studio-secret-dialog")
        && target.id !== "t-persona-id" && target.id !== "d-persona-id") {
      lastStudioSecretTarget = target;
    }
  });
  fetch("/api/secrets/status")
    .then(r => r.json())
    .then(d => { secretArea.style.display = d.enabled ? "" : "none"; })
    .catch(() => { secretArea.style.display = "none"; });
  document.getElementById("studio-secret-add").addEventListener("click", () => {
    if (!lastStudioSecretTarget) {
      setStatus(t("studioSecretTargetRequired"), true);
      return;
    }
    document.getElementById("studio-secret-label").value = "";
    document.getElementById("studio-secret-value").value = "";
    secretDialog.showModal();
    document.getElementById("studio-secret-value").focus();
  });
  document.getElementById("studio-secret-cancel").addEventListener("click", () => secretDialog.close());
  secretForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const label = document.getElementById("studio-secret-label").value.trim();
    const valueEl = document.getElementById("studio-secret-value");
    const value = valueEl.value.trim();
    if (!value) return;
    const submit = secretForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const res = await fetch("/api/secrets/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, value }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "register failed");
      studioSecrets.push({ label: data.label || label, placeholder: data.placeholder });
      renderStudioSecrets();
      insertStudioPlaceholder(data.placeholder);
      secretDialog.close();
      valueEl.value = "";
    } catch (err) {
      setStatus(t("secretRegisterError") + ": " + err.message, true);
    } finally {
      submit.disabled = false;
    }
  });
  renderStudioSecrets();

  // t ↔ d のpersona-id同期（バリデーション付き）
  document.getElementById("t-persona-id").addEventListener("input", () => {
    syncPersonaIdAndValidate("t-persona-id", "d-persona-id");
  });
  document.getElementById("d-persona-id").addEventListener("input", () => {
    syncPersonaIdAndValidate("d-persona-id", "t-persona-id");
  });

  const testMsg = document.getElementById("test-msg");
  testMsg.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doTestChat(); }
  });
  testMsg.addEventListener("input", () => {
    testMsg.style.height = "auto";
    testMsg.style.height = Math.min(testMsg.scrollHeight, parseFloat(getComputedStyle(testMsg).lineHeight) * 3 + 16) + "px";
  });

  // raw-text 文字数カウンター
  const rawText = document.getElementById("raw-text");
  const rawCount = document.getElementById("raw-text-count");
  if (rawText && rawCount) {
    const updateCount = () => {
      const len = rawText.value.length;
      rawCount.textContent = len > 0 ? `${len} 文字` : "";
    };
    rawText.addEventListener("input", updateCount);
    updateCount();
  }

  // ドラフト自動読込（persona_id が入力済みの場合）
  const initPersonaId = document.getElementById("t-persona-id").value.trim();
  if (initPersonaId && initPersonaId !== defaultPersonaId()) {
    loadFormDraft(initPersonaId);
  }
});

window.addEventListener("beforeunload", (e) => {
  // 抽出・生成中にリロードされた場合、バックエンドにキャンセル通知
  if (_loading) {
    navigator.sendBeacon("/api/persona-studio/cancel");
  }
  if (hasDraft) e.preventDefault();
});
