/* RP Standalone — Settings UI */

let currentConfig = null;
let originalConfig = null;
let memoryStats = null;
let memoryRecords = [];
let memorySelected = new Set();
let memoryLoading = false;

/* ── Init ── */

// ボタンイベントを先に設定（DOMContentLoaded 非依存、スクリプト末尾なのでDOM構築済み）
(function bindButtons() {
  const addBtn = document.getElementById('add-chain-entry-btn');
  if (addBtn) addBtn.addEventListener('click', addChainEntry);

  const applyBtn = document.getElementById('apply-extraction-btn');
  if (applyBtn) applyBtn.addEventListener('click', applyExtractionChain);
})();

document.addEventListener('DOMContentLoaded', async () => {
  try {
    i18nApply();
    updateLangToggle();
    document.getElementById('provider-select').addEventListener('change', onProviderChange);
    document.getElementById('model-textarea').addEventListener('input', onModelTextareaChange);
    document.getElementById('model-select').addEventListener('change', onActiveModelChange);
    document.getElementById('apply-provider-btn').addEventListener('click', applyProvider);
    document.getElementById('apply-api-btn').addEventListener('click', applyApiParams);
    document.getElementById('apply-watchdog-btn').addEventListener('click', applyWatchdog);
    document.getElementById('global-narration').addEventListener('change', updateGlobalPersonToggle);
    document.getElementById('apply-style-btn').addEventListener('click', applyGlobalStyle);
    document.getElementById('apply-session-btn').addEventListener('click', applySessionSettings);
    document.getElementById('system-prompt-textarea').addEventListener('input', onSystemPromptInput);
    document.getElementById('apply-system-prompt-btn').addEventListener('click', applySystemPrompt);
    document.getElementById('reset-all-settings-btn').addEventListener('click', resetAllSettings);
    bindMemoryManager();

    // タブ切替
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => showTab(btn.dataset.tab));
    });

    await loadConfig();
    populateProviderSelect();
    // populateProviderSelect() で option が再構築された後にアクティブ値を設定
    document.getElementById('provider-select').value = currentConfig.active_provider || '';
    onProviderChange();
    loadExtractionChain();
    populateWatchdogLevels();
    populatePersonaStyles();
    loadGlobalStyle();
    loadAdvancedSettings();
    loadSystemPrompt();
    const requestedTab = new URLSearchParams(location.search).get('tab');
    if (requestedTab && document.getElementById(`tab-${requestedTab}`)) showTab(requestedTab);
  } catch (e) {
    console.error('Settings init error:', e);
    showToast('設定初期化エラー: ' + (e.message || e), true);
  }
});

async function loadConfig() {
  try {
    const res = await fetch('/api/config/full');
    currentConfig = await res.json();
    originalConfig = JSON.parse(JSON.stringify(currentConfig));

    // 現在の表示
    document.getElementById('current-provider').textContent = currentConfig.active_provider || '—';
    document.getElementById('current-model').textContent = currentConfig.active_model || '—';

    // API パラメータ
    const api = currentConfig.api || {};
    document.getElementById('api-max-tokens').value = api.max_tokens || 2000;
    document.getElementById('api-temperature').value = api.temperature || 0.8;
    document.getElementById('api-timeout').value = api.timeout || 120;

  } catch (err) {
    console.error('loadConfig error:', err);
    showToast('設定読み込み失敗: ' + err.message, true);
  }
}

/* ── Provider / Model ── */

function populateProviderSelect() {
  const select = document.getElementById('provider-select');
  select.replaceChildren();
  // プレースホルダ
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = '選択してください';
  ph.disabled = true;
  select.appendChild(ph);
  for (const [pid] of Object.entries(currentConfig.providers || {})) {
    const opt = document.createElement('option');
    opt.value = pid;
    opt.textContent = pid.charAt(0).toUpperCase() + pid.slice(1);
    select.appendChild(opt);
  }
}

function onProviderChange() {
  const providerId = document.getElementById('provider-select').value;
  const textarea = document.getElementById('model-textarea');

  if (providerId && currentConfig.providers[providerId]) {
    const models = currentConfig.providers[providerId].models || [];
    textarea.value = models.join('\n');
    updateModelFormatHint(providerId);
  } else {
    textarea.value = '';
  }
  syncModelsFromTextarea();
}

function updateModelFormatHint(providerId) {
  const hint = document.getElementById('model-format-hint');
  if (!hint) return;
  const pdata = currentConfig.providers[providerId] || {};
  const baseUrl = pdata.base_url || '';
  if (baseUrl.includes('openrouter')) {
    hint.textContent = 'OpenRouter形式: provider/model（例: openai/gpt-4o, google/gemini-2.5-flash）';
  } else {
    hint.textContent = 'モデル名をそのまま入力（例: ' + (pdata.models?.[0] || 'gpt-4o') + '）';
  }
}

function onModelTextareaChange() {
  syncModelsFromTextarea();
}

function onActiveModelChange() {
  // 選択変更時は何もしない（applyProvider で読む）
}

function syncModelsFromTextarea() {
  const textarea = document.getElementById('model-textarea');
  const modelSelect = document.getElementById('model-select');
  const lines = textarea.value.split('\n').map(s => s.trim()).filter(s => s);
  const currentActive = currentConfig.active_model;

  modelSelect.replaceChildren();
  lines.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    modelSelect.appendChild(opt);
  });

  // 現在の active_model がリスト内にあれば選択、なければ先頭
  if (lines.includes(currentActive)) {
    modelSelect.value = currentActive;
  } else if (lines.length > 0) {
    modelSelect.value = lines[0];
  }
}

async function applyProvider() {
  const provider = document.getElementById('provider-select').value;
  const model = document.getElementById('model-select').value;
  const textarea = document.getElementById('model-textarea');
  const models = textarea.value.split('\n').map(s => s.trim()).filter(s => s);

  if (!provider) {
    showToast('プロバイダを選択してください', true);
    return;
  }
  if (models.length === 0) {
    showToast('モデルを1つ以上入力してください', true);
    return;
  }

  try {
    const res = await fetch('/api/config/provider', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model, models }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('プロバイダ/モデルを変更しました。再接続します...');
    setTimeout(() => location.reload(), 1500);
  } catch (err) {
    showToast('変更失敗: ' + err.message, true);
  }
}

async function applyApiParams() {
  const api = {
    max_tokens: parseInt(document.getElementById('api-max-tokens').value) || 2000,
    temperature: parseFloat(document.getElementById('api-temperature').value) || 0.8,
    timeout: parseInt(document.getElementById('api-timeout').value) || 120,
  };

  try {
    const res = await fetch('/api/config/api', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('API パラメータを適用しました');
    currentConfig.api = api;
  } catch (err) {
    showToast('適用失敗: ' + err.message, true);
  }
}

/* ── Watchdog ── */

function populateWatchdogLevels() {
  const container = document.getElementById('escalation-levels');
  const watchdog = currentConfig.watchdog || { levels: [] };
  const levels = watchdog.levels || [];
  document.getElementById('watchdog-enabled').checked = watchdog.enabled !== false;
  document.getElementById('watchdog-interval').value = watchdog.check_interval || 30;
  container.replaceChildren();
  levels.forEach((level, index) => {
    const section = document.createElement('div'); const heading = document.createElement('h4');
    section.className = 'escalation-level'; heading.textContent = `${t('levelLabel', {n: index + 1})} (${level.after}秒後)`; section.appendChild(heading);
    const addRow = (labelText, control) => { const row = document.createElement('div'); const label = document.createElement('label'); row.className = 'setting-row'; label.textContent = labelText; row.append(label, control); section.appendChild(row); };
    const after = document.createElement('input'); after.type = 'number'; after.className = 'level-after'; after.value = level.after; after.min = '10'; after.max = '86400'; after.step = '10';
    const subject = document.createElement('input'); subject.type = 'text'; subject.className = 'level-subject'; subject.value = level.subject || '';
    const body = document.createElement('textarea'); body.className = 'level-body'; body.rows = 3; body.value = level.body || '';
    addRow(t('labelAfter'), after); addRow(t('labelSubject'), subject); addRow(t('labelBody'), body); container.appendChild(section);
  });
}
async function applyWatchdog() {
  const levels = [];
  document.querySelectorAll('.escalation-level').forEach(el => {
    levels.push({
      after: parseInt(el.querySelector('.level-after').value) || 300,
      subject: el.querySelector('.level-subject').value || '',
      body: el.querySelector('.level-body').value || '',
    });
  });

  const watchdog = {
    enabled: document.getElementById('watchdog-enabled').checked,
    check_interval: parseInt(document.getElementById('watchdog-interval').value) || 30,
    levels,
  };

  try {
    const res = await fetch('/api/config/watchdog', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ watchdog }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('Watchdog 設定を適用しました');
    currentConfig.watchdog = watchdog;
  } catch (err) {
    showToast('適用失敗: ' + err.message, true);
  }
}

/* ── Global Style ── */

function loadGlobalStyle() {
  const style = currentConfig.style || {};
  document.getElementById('global-viewpoint').value = style.viewpoint || 'ai_character';
  document.getElementById('global-person').value = style.person || 'first';
  document.getElementById('global-narration').value = (style.narration !== false) ? 'true' : 'false';
  updateGlobalPersonToggle();
}

function updateGlobalPersonToggle() {
  const nar = document.getElementById('global-narration').value === 'true';
  document.getElementById('global-person-row').style.opacity = nar ? '1' : '0.35';
  document.getElementById('global-person').disabled = !nar;
}

async function applyGlobalStyle() {
  const style = {
    viewpoint: document.getElementById('global-viewpoint').value,
    person: document.getElementById('global-person').value,
    narration: document.getElementById('global-narration').value === 'true',
  };

  try {
    const res = await fetch('/api/config/style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('グローバル文体設定を保存しました（次回新規セッションで反映）');
    if (!currentConfig.style) currentConfig.style = {};
    Object.assign(currentConfig.style, data.style);
  } catch (err) {
    showToast('保存失敗: ' + err.message, true);
  }
}

/* ── Persona Styles ── */

async function populatePersonaStyles() {
  const container = document.getElementById('persona-style-list');
  container.replaceChildren();
  try {
    const personasRes = await fetch('/api/persona/list');
    const personas = await personasRes.json();

    for (const p of personas) {
      const personaId = String(p.id ?? '');
      const styleRes = await fetch(`/api/persona/${encodeURIComponent(personaId)}/style`);
      const styleData = await styleRes.json();
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      const content = document.createElement('div');
      const button = document.createElement('button');
      details.className = 'persona-style-detail';
      content.className = 'style-detail-content';

      if (styleData.status === 'ok') {
        summary.textContent = `${String(p.name ?? '')} (${personaId})`;
        const defaultLine = document.createElement('p');
        const defaultLabel = document.createElement('strong');
        defaultLabel.textContent = `${t('defaultStyle')}: `;
        defaultLine.append(defaultLabel, document.createTextNode(presetDescription(styleData.default_style || {})));

        const presetsLabel = document.createElement('p');
        const presetsStrong = document.createElement('strong');
        presetsStrong.textContent = `${t('presetsLabel')}:`;
        presetsLabel.appendChild(presetsStrong);
        const list = document.createElement('ul');
        (styleData.presets || []).forEach(preset => {
          const item = document.createElement('li');
          item.textContent = `${String(preset.label ?? '')}: ${presetDescription(preset.style)}`;
          list.appendChild(item);
        });
        button.className = 'btn btn-secondary btn-sm';
        button.dataset.i18n = 'btnEditInStudio';
        button.textContent = t('btnEditInStudio');
        content.append(defaultLine, presetsLabel, list, button);
      } else {
        summary.textContent = `${String(p.name ?? '')} (${personaId}) — ${t('styleNotConfigured')}`;
        button.className = 'btn btn-primary btn-sm';
        button.dataset.i18n = 'btnCreateInStudio';
        button.textContent = t('btnCreateInStudio');
        content.appendChild(button);
      }

      button.addEventListener('click', () => editPersonaStyle(personaId));
      details.append(summary, content);
      container.appendChild(details);
    }
  } catch (err) {
    console.error('populatePersonaStyles error:', err);
    const error = document.createElement('p');
    error.className = 'load-error';
    error.textContent = '読み込み失敗';
    container.replaceChildren(error);
  }
}

function editPersonaStyle(personaId) {
  location.href = `/studio?edit=${encodeURIComponent(personaId)}`;
}

/* ── Advanced Settings ── */

function loadAdvancedSettings() {
  // config.yaml から読み取った値を表示（読み取り専用）
  document.getElementById('session-max-tokens').value = currentConfig.session?.max_tokens || 32000;
  document.getElementById('session-save-interval').value = currentConfig.session?.save_interval || 1;

  // パス設定（config.yaml から）
  document.getElementById('path-chroma').value = currentConfig.chroma?.path || '';
  document.getElementById('path-embedding').value = currentConfig.chroma?.embedding_model || '';
  document.getElementById('path-personas').value = currentConfig.personas_dir || '../personas';
}

async function applySessionSettings() {
  const session = {
    max_tokens: parseInt(document.getElementById('session-max-tokens').value) || 32000,
    save_interval: parseInt(document.getElementById('session-save-interval').value) || 1,
  };

  try {
    const res = await fetch('/api/config/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('セッション設定を適用しました（再起動で反映）');
    currentConfig.session = session;
  } catch (err) {
    showToast('適用失敗: ' + err.message, true);
  }
}

async function resetAllSettings() {
  if (!confirm(t('confirmReset') || '全設定をデフォルトに戻しますか？')) return;

  try {
    const res = await fetch('/api/config/reset', {
      method: 'POST',
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast('設定をリセットしました。再読込します...');
    setTimeout(() => location.reload(), 1500);
  } catch (err) {
    showToast('リセット失敗: ' + err.message, true);
  }
}

/* ── Tab Navigation ── */

function showTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  document.querySelector(`.tab-btn[data-tab="${tabId}"]`)?.classList.add('active');
  document.getElementById(`tab-${tabId}`)?.classList.add('active');
  if (tabId === 'memory' && !memoryStats) loadMemoryDb();
}

/* ── Memory DB ── */

function bindMemoryManager() {
  document.getElementById('memory-refresh-btn').addEventListener('click', loadMemoryDb);
  document.getElementById('memory-persona-filter').addEventListener('change', () => {
    memorySelected.clear();
    populateMemorySessionFilter();
    renderMemoryRecords();
  });
  document.getElementById('memory-session-filter').addEventListener('change', () => {
    memorySelected.clear();
    renderMemoryRecords();
  });
  document.getElementById('memory-kind-filter').addEventListener('change', () => {
    memorySelected.clear();
    renderMemoryRecords();
  });
  document.getElementById('memory-select-all').addEventListener('change', event => {
    filteredMemoryRecords().forEach(record => {
      if (event.target.checked) memorySelected.add(record.id);
      else memorySelected.delete(record.id);
    });
    renderMemoryRecords();
  });
  document.getElementById('memory-delete-selected').addEventListener('click', () => deleteMemoryScope('records'));
  document.getElementById('memory-delete-persona').addEventListener('click', () => deleteMemoryScope('persona'));
  document.getElementById('memory-delete-session').addEventListener('click', () => deleteMemoryScope('session'));
  document.getElementById('memory-delete-orphans').addEventListener('click', () => deleteMemoryScope('orphans'));
  document.getElementById('memory-delete-all').addEventListener('click', () => deleteMemoryScope('all'));
}

async function memoryFetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setMemoryBusy(busy) {
  memoryLoading = busy;
  document.querySelectorAll('#tab-memory button, #tab-memory select, #tab-memory input').forEach(control => {
    control.disabled = busy;
  });
  if (!busy) updateMemoryActions();
}

async function loadMemoryDb() {
  if (memoryLoading) return;
  setMemoryBusy(true);
  const unavailable = document.getElementById('memory-unavailable');
  const manager = document.getElementById('memory-manager');
  try {
    const [stats, records] = await Promise.all([
      memoryFetchJson('/api/memory/stats'),
      memoryFetchJson('/api/memory/records'),
    ]);
    memoryStats = stats;
    memoryRecords = Array.isArray(records.items) ? records.items : [];
    memorySelected.clear();
    unavailable.classList.add('is-hidden');
    manager.classList.remove('is-hidden');
    renderMemoryStats();
    populateMemoryPersonaFilter();
    populateMemorySessionFilter();
    renderMemoryRecords();
  } catch (err) {
    memoryStats = null;
    memoryRecords = [];
    unavailable.textContent = `${t('memoryUnavailable')}: ${err.message}`;
    unavailable.classList.remove('is-hidden');
    manager.classList.add('is-hidden');
  } finally {
    setMemoryBusy(false);
  }
}

function renderMemoryStats() {
  const kinds = memoryStats?.by_kind || {};
  document.getElementById('memory-stat-total').textContent = String(memoryStats?.total || 0);
  document.getElementById('memory-stat-session').textContent = String(kinds.session_fact || 0);
  document.getElementById('memory-stat-persona').textContent = String(kinds.persona_base || 0);
  document.getElementById('memory-stat-legacy').textContent = String(kinds.legacy || 0);
  document.getElementById('memory-stat-orphans').textContent = String(memoryStats?.orphan_session_facts || 0);
}

function replaceMemoryOptions(select, values, firstLabel) {
  const current = select.value;
  const first = document.createElement('option');
  first.value = '';
  first.textContent = firstLabel;
  select.replaceChildren(first);
  values.forEach(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  select.value = values.includes(current) ? current : '';
}

function populateMemoryPersonaFilter() {
  const values = [...new Set(memoryRecords.map(record => record.persona_id).filter(Boolean))].sort();
  replaceMemoryOptions(document.getElementById('memory-persona-filter'), values, t('memoryAllPersonas'));
}

function populateMemorySessionFilter() {
  const persona = document.getElementById('memory-persona-filter').value;
  const values = [...new Set(memoryRecords
    .filter(record => !persona || record.persona_id === persona)
    .map(record => record.session_id)
    .filter(Boolean))].sort();
  replaceMemoryOptions(document.getElementById('memory-session-filter'), values, t('memoryAllSessions'));
}

function filteredMemoryRecords() {
  const persona = document.getElementById('memory-persona-filter').value;
  const session = document.getElementById('memory-session-filter').value;
  const kind = document.getElementById('memory-kind-filter').value;
  return memoryRecords.filter(record =>
    (!persona || record.persona_id === persona)
    && (!session || record.session_id === session)
    && (!kind || record.kind === kind)
  );
}

function renderMemoryRecords() {
  const tbody = document.getElementById('memory-records-body');
  const visible = filteredMemoryRecords();
  tbody.replaceChildren();
  visible.forEach(record => {
    const row = document.createElement('tr');
    if (record.orphan) row.classList.add('is-orphan');
    const checkCell = document.createElement('td');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = memorySelected.has(record.id);
    checkbox.setAttribute('aria-label', `Select ${record.id}`);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) memorySelected.add(record.id);
      else memorySelected.delete(record.id);
      updateMemoryActions();
    });
    checkCell.appendChild(checkbox);
    const addCell = value => {
      const cell = document.createElement('td');
      cell.textContent = value || '—';
      row.appendChild(cell);
    };
    row.appendChild(checkCell);
    addCell(record.kind);
    addCell(record.persona_id);
    addCell(record.session_id);
    const sourceCell = document.createElement('td');
    const source = document.createElement('div');
    const id = document.createElement('small');
    source.textContent = record.source || (record.orphan ? t('memoryOrphanLabel') : '—');
    id.textContent = record.id;
    id.title = record.id;
    sourceCell.append(source, id);
    row.appendChild(sourceCell);
    tbody.appendChild(row);
  });
  const selectedVisible = visible.filter(record => memorySelected.has(record.id)).length;
  const selectAll = document.getElementById('memory-select-all');
  selectAll.checked = visible.length > 0 && selectedVisible === visible.length;
  selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
  document.getElementById('memory-list-summary').textContent = t('memoryListSummary', {visible: visible.length, total: memoryRecords.length});
  updateMemoryActions();
}

function updateMemoryActions() {
  if (memoryLoading || !memoryStats) return;
  const persona = document.getElementById('memory-persona-filter').value;
  const session = document.getElementById('memory-session-filter').value;
  const visible = filteredMemoryRecords();
  document.getElementById('memory-delete-selected').disabled = memorySelected.size === 0;
  document.getElementById('memory-delete-persona').disabled = !persona || !memoryRecords.some(record => record.persona_id === persona);
  document.getElementById('memory-delete-session').disabled = !persona || !session || !memoryRecords.some(record => record.persona_id === persona && record.session_id === session);
  document.getElementById('memory-delete-orphans').disabled = !(memoryStats.orphan_session_facts > 0);
  document.getElementById('memory-delete-all').disabled = !(memoryStats.total > 0);
  document.getElementById('memory-select-all').disabled = visible.length === 0;
}

async function deleteMemoryScope(scope) {
  if (memoryLoading) return;
  const persona = document.getElementById('memory-persona-filter').value;
  const session = document.getElementById('memory-session-filter').value;
  const payload = {scope};
  let count = 0;
  let target = scope;
  if (scope === 'records') {
    payload.ids = [...memorySelected];
    count = payload.ids.length;
    target = t('memorySelectedRecords');
  } else if (scope === 'persona') {
    payload.persona_id = persona;
    count = memoryRecords.filter(record => record.persona_id === persona).length;
    target = `persona ${persona}`;
  } else if (scope === 'session') {
    payload.persona_id = persona;
    payload.session_id = session;
    count = memoryRecords.filter(record => record.persona_id === persona && record.session_id === session && record.kind === 'session_fact').length;
    target = `session ${persona}/${session}`;
  } else {
    try {
      const latestStats = await memoryFetchJson('/api/memory/stats');
      count = scope === 'all' ? latestStats.total : latestStats.orphan_session_facts;
      target = scope === 'all' ? t('memoryAllRecords') : t('memoryOrphanRecords');
    } catch (err) {
      showToast(`${t('memoryDeleteFailed')}: ${err.message}`, true);
      return;
    }
  }
  if (!count) return;
  if (!confirm(t('memoryDeleteConfirm', {target, count}))) return;
  setMemoryBusy(true);
  try {
    const result = await memoryFetchJson('/api/memory/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    showToast(t('memoryDeleted', {count: result.deleted_count}));
    memoryStats = null;
  } catch (err) {
    showToast(`${t('memoryDeleteFailed')}: ${err.message}`, true);
  } finally {
    setMemoryBusy(false);
  }
  await loadMemoryDb();
}

/* ── System Prompt ── */

const SYSTEM_PROMPT_LIMIT = 1500;

function loadSystemPrompt() {
  const textarea = document.getElementById('system-prompt-textarea');
  const prompt = currentConfig.global_system_prompt || '';
  textarea.value = prompt;
  updateSystemPromptCounter();
}

function onSystemPromptInput() {
  updateSystemPromptCounter();
}

function updateSystemPromptCounter() {
  const textarea = document.getElementById('system-prompt-textarea');
  const counter = document.getElementById('system-prompt-counter');
  const warning = document.getElementById('system-prompt-warning');
  const len = textarea.value.length;

  counter.textContent = `${len} / ${SYSTEM_PROMPT_LIMIT} 文字`;
  if (len > SYSTEM_PROMPT_LIMIT) {
    counter.style.color = 'var(--warning)';
    warning.style.display = 'block';
  } else {
    counter.style.color = '';
    warning.style.display = 'none';
  }
}

async function applySystemPrompt() {
  const textarea = document.getElementById('system-prompt-textarea');
  const system_prompt = textarea.value;

  try {
    const res = await fetch('/api/config/system-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ system_prompt }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast(`システムプロンプトを保存しました（${data.char_count} 文字）`);
    currentConfig.global_system_prompt = system_prompt;
  } catch (err) {
    showToast('保存失敗: ' + err.message, true);
  }
}

/* ── Reset ── */

function showToast(msg, isError) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.style.background = isError ? 'var(--error)' : 'var(--accent)';
  toast.style.display = 'block';
  setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

/* ── i18n (settings extensions — merged into i18n.js I18N) ── */

Object.assign(I18N.ja, {
    langToggle: 'EN',
    settingsTitle: '設定',
    navSessions: 'セッション',
    btnBackToSessions: 'セッション一覧へ',
    tabProvider: 'プロバイダ / モデル',
    tabWatchdog: 'Watchdog',
    tabStyle: '文体 / スタイル',
    tabAdvanced: '詳細',
    secProvider: 'プロバイダ選択',
    labelProvider: 'プロバイダ',
    hintProvider: 'APIリクエストの送信先です。プロバイダを切り替えると利用可能なモデル一覧が変わります。',
    labelModel: 'モデル (1行1つ)',
    labelActiveModel: 'アクティブモデル',
    hintActiveModel: 'チャット・RPで実際に使われるモデルです。変更後は「適用して再接続」で反映されます。',
    hintModelFormat: 'OpenRouter系は provider/model 形式（例: openai/gpt-4o）、直接APIはモデル名のみ',
    currentProvider: 'プロバイダ',
    currentModel: 'モデル',
    btnApply: '適用',
    secApiParams: 'API 共通パラメータ',
    labelMaxTokens: '出力 Max Tokens',
    hintMaxTokens: '1回の応答で生成される最大トークン数（出力の長さの上限）。地の文が多いRPでは3000〜4000が目安。',
    labelTemperature: 'Temperature',
    labelTimeout: 'Timeout (秒)',
    secWatchdog: '放置検知設定',
    labelWatchdogEnabled: '有効',
    hintWatchdogEnabled: '有効にすると、一定時間操作がない場合にメール通知を行います（Gmail SMTP設定が必要）。',
    labelCheckInterval: 'チェック間隔 (秒)',
    secEscalation: 'エスカレーション段階',
    levelLabel: 'レベル {n}',
    labelAfter: '経過秒数',
    labelSubject: '件名',
    labelBody: '本文',
    secStyleGlobal: 'グローバル文体設定（セッション未開始時のデフォルト）',
    customViewpoint: '語り手',
    customNarration: '地の文',
    customPerson: '人称',
    optAIChar: 'AIキャラ視点',
    optUserChar: 'ユーザーキャラ視点',
    optNarrationOn: 'あり',
    optNarrationOff: 'なし',
    optFirstPerson: '一人称',
    optThirdPerson: '三人称',
    secPersonaStyles: 'ペルソナ別スタイル（style.yaml 連動）',
    styleHint: '各ペルソナの personas/{id}/style.yaml で管理されます。Persona Studio で編集してください。',
    defaultStyle: 'デフォルト',
    presetsLabel: 'プリセット',
    styleNotConfigured: '未設定',
    btnEditInStudio: 'Studioで編集',
    btnCreateInStudio: 'Studioで作成',
    secSession: 'セッション設定',
    labelMaxContextTokens: '最大コンテキストトークン',
    hintMaxContextTokens: '会話履歴として保持する最大トークン数。長いRPほど大きな値が必要です。',
    labelSaveInterval: '保存間隔（往復数）',
    hintSaveInterval: '何往復ごとに履歴をファイルに保存するか（通常は1でOK）',
    secPaths: 'パス設定（config.yaml で管理）',
    labelChromaPath: 'ChromaDB パス',
    labelEmbeddingModel: '埋め込みモデル',
    labelPersonasDir: 'Personas ディレクトリ',
    secDanger: '危険な操作',
    btnResetAll: '全設定リセット（config.yaml 書き戻し）',
    confirmReset: '全設定をデフォルトに戻しますか？',
    hintTemperature: '低いほど安定、高いほど多様な応答（会話は0.7〜1.0が目安）',
    hintTimeout: 'API応答の最大待ち時間。長文生成時は長めに',
    hintCheckInterval: '放置検知の確認頻度。短すぎると負荷、長すぎると検知が遅れます',
    tabExtraction: '抽出モデル',
    secExtractionChain: '抽出タスク用フォールバックチェーン',
    hintExtractionChain: 'ペルソナスタジオのフィールド抽出・SOUL/SKILL生成で、上から順に試行します。最初に成功したモデルが使われます。全滅時はエラーになります。',
    hintChainMax: '最大5つまで。空のまま保存するとチャット用モデルが使われます。',
    btnAddChainEntry: '＋ 優先枠を追加',
    chainPriority: '優先{n}',
    chainSelectProvider: '選択してください',
    chainRemove: '削除',
    chainMaxReached: '最大{n}個までです',
    chainMinOne: '最低1つは必要です',
    chainSaved: '抽出タスク用モデル設定を保存しました',
    chainSaveFailed: '保存失敗',
    secSystemPrompt: 'システムプロンプト（共通指示）',
    hintSystemPrompt: '全モデル・全ペルソナの応答に適用される共通指示です。推奨は1500文字以内（長すぎると会話履歴が圧迫されます）。',
    tabMemory: 'Memory DB',
    secMemoryDb: 'Memory DB 管理',
    hintMemoryDb: '本文やembeddingは表示せず、metadataだけを管理します。',
    btnRefresh: '再読込',
    memoryTotal: '全件',
    memoryOrphans: '孤児',
    memoryAllPersonas: '全persona',
    memoryAllSessions: '全session',
    memoryAllKinds: '全kind',
    memoryDeleteSelected: '選択削除',
    memoryDeletePersona: 'persona全削除',
    memoryDeleteSession: 'session全削除',
    memoryDeleteOrphans: '孤児全削除',
    memoryDeleteAll: '全件削除',
    memoryUnavailable: 'Memory DBを利用できません',
    memoryOrphanLabel: '孤児session_fact',
    memoryListSummary: '{total}件中 {visible}件を表示',
    memorySelectedRecords: '選択record',
    memoryAllRecords: 'Memory DB 全record',
    memoryOrphanRecords: '孤児record',
    memoryDeleteConfirm: '{target} {count}件を削除しますか？',
    memoryDeleted: '{count}件を削除しました',
    memoryDeleteFailed: '削除失敗',
});
Object.assign(I18N.en, {
    langToggle: '日本語',
    settingsTitle: 'Settings',
    navSessions: 'Sessions',
    btnBackToSessions: 'Back to Sessions',
    tabProvider: 'Provider / Model',
    tabWatchdog: 'Watchdog',
    tabStyle: 'Style',
    tabAdvanced: 'Advanced',
    secProvider: 'Provider Selection',
    labelProvider: 'Provider',
    hintProvider: 'API endpoint for model requests. Switching providers changes the available model list.',
    labelModel: 'Models (one per line)',
    labelActiveModel: 'Active Model',
    hintActiveModel: 'The model used for chat and RP. Changes take effect after applying and reconnecting.',
    hintModelFormat: 'OpenRouter: provider/model format. Direct API: model name only.',
    currentProvider: 'Provider',
    currentModel: 'Model',
    btnApply: 'Apply',
    secApiParams: 'API Parameters',
    labelMaxTokens: '出力 Max Tokens',
    hintMaxTokens: '1回の応答で生成される最大トークン数（出力の長さの上限）。地の文が多いRPでは3000〜4000が目安。',
    labelTemperature: 'Temperature',
    labelTimeout: 'Timeout (sec)',
    secWatchdog: 'Watchdog Settings',
    labelWatchdogEnabled: 'Enabled',
    hintWatchdogEnabled: 'When enabled, sends email notifications after a period of inactivity (requires Gmail SMTP).',
    labelCheckInterval: 'Check Interval (sec)',
    secEscalation: 'Escalation Levels',
    levelLabel: 'Level {n}',
    labelAfter: 'After (sec)',
    labelSubject: 'Subject',
    labelBody: 'Body',
    secStyleGlobal: 'Global Style (default for new sessions)',
    customViewpoint: 'Narrator',
    customNarration: 'Narration',
    customPerson: 'Person',
    optAIChar: 'AI char. view',
    optUserChar: 'User char. view',
    optNarrationOn: 'On',
    optNarrationOff: 'Off',
    optFirstPerson: 'First-person',
    optThirdPerson: 'Third-person',
    secPersonaStyles: 'Persona Styles (style.yaml)',
    styleHint: 'Managed in personas/{id}/style.yaml. Edit in Studio.',
    defaultStyle: 'Default',
    presetsLabel: 'Presets',
    styleNotConfigured: 'Not configured',
    btnEditInStudio: 'Edit in Studio',
    btnCreateInStudio: 'Create in Studio',
    secSession: 'Session Settings',
    labelMaxContextTokens: 'Max Context Tokens',
    hintMaxContextTokens: 'Maximum tokens retained as conversation history. Longer RPs need larger values.',
    labelSaveInterval: 'Save Interval (turns)',
    hintSaveInterval: 'How many turns between file saves (usually 1)',
    secPaths: 'Paths (from config.yaml)',
    labelChromaPath: 'ChromaDB Path',
    labelEmbeddingModel: 'Embedding Model',
    labelPersonasDir: 'Personas Directory',
    secDanger: 'Danger Zone',
    btnResetAll: 'Reset All Settings',
    confirmReset: 'Reset all settings to defaults?',
    hintTemperature: 'Lower = more stable, higher = more creative (0.7–1.0 for chat)',
    hintTimeout: 'Max wait time for API response. Increase for long generations',
    hintCheckInterval: 'How often to check for inactivity. Too short = overhead, too long = slow detection',
    tabExtraction: 'Extraction Model',
    secExtractionChain: 'Extraction Fallback Chain',
    hintExtractionChain: 'For Persona Studio field extraction and SOUL/SKILL generation. Tried top-to-bottom; first success wins. All-fail = error.',
    hintChainMax: 'Max 5 entries. Save empty to use chat model as default.',
    btnAddChainEntry: '+ Add Priority Slot',
    chainPriority: 'Priority {n}',
    chainSelectProvider: 'Select provider',
    chainRemove: 'Remove',
    chainMaxReached: 'Max {n} entries',
    chainMinOne: 'At least 1 required',
    chainSaved: 'Extraction model settings saved',
    chainSaveFailed: 'Save failed',
    secSystemPrompt: 'System Prompt (Global Instruction)',
    hintSystemPrompt: 'A common instruction applied to all model responses across all personas. Recommended within 1500 chars (longer prompts reduce available context).',
    tabMemory: 'Memory DB',
    secMemoryDb: 'Memory DB Management',
    hintMemoryDb: 'Manage metadata only; documents and embeddings are never displayed.',
    btnRefresh: 'Refresh',
    memoryTotal: 'Total',
    memoryOrphans: 'Orphans',
    memoryAllPersonas: 'All personas',
    memoryAllSessions: 'All sessions',
    memoryAllKinds: 'All kinds',
    memoryDeleteSelected: 'Delete selected',
    memoryDeletePersona: 'Delete persona records',
    memoryDeleteSession: 'Delete session records',
    memoryDeleteOrphans: 'Delete all orphans',
    memoryDeleteAll: 'Delete all records',
    memoryUnavailable: 'Memory DB is unavailable',
    memoryOrphanLabel: 'Orphan session_fact',
    memoryListSummary: 'Showing {visible} of {total}',
    memorySelectedRecords: 'selected records',
    memoryAllRecords: 'all Memory DB records',
    memoryOrphanRecords: 'orphan records',
    memoryDeleteConfirm: 'Delete {count} {target}?',
    memoryDeleted: 'Deleted {count} records',
    memoryDeleteFailed: 'Delete failed',
});

/* ── enhanced t() with vars support (override i18n.js) ── */

function t(key, vars) {
  const l = getLang();
  let txt = I18N[l]?.[key] || I18N.ja[key] || key;
  if (vars) Object.entries(vars).forEach(([k, v]) => { txt = txt.replace('{' + k + '}', v); });
  return txt;
}
function i18nApply() {
  const l = getLang();
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.getAttribute('data-i18n');
    if (I18N[l]?.[k]) el.textContent = I18N[l][k];
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const k = el.getAttribute('data-i18n-placeholder');
    if (I18N[l]?.[k]) el.placeholder = I18N[l][k];
  });
  document.querySelectorAll('[data-i18n-value]').forEach(el => {
    const k = el.getAttribute('data-i18n-value');
    if (I18N[l]?.[k]) el.textContent = I18N[l][k];
  });
}
function toggleLang() {
  const next = getLang() === 'ja' ? 'en' : 'ja';
  setLang(next);
  i18nApply();
  updateLangToggle();
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function presetDescription(style) {
  if (!style) return '';
  const v = { ai_character: 'AI視点', user_character: 'ユーザー視点' };
  const p = { first: '一人称', third: '三人称' };
  const n = style.narration ? '地の文あり' : '地の文なし';
  return `[${v[style.viewpoint] || style.viewpoint}・${n}${style.narration ? '・' + p[style.person] : ''}]`;
}

/* ── Extraction Fallback Chain ── */

const MAX_CHAIN_ENTRIES = 5;

function loadExtractionChain() {
  try {
    const list = document.getElementById('extraction-chain-list');
    if (!list) { console.error('loadExtractionChain: #extraction-chain-list not found'); return; }

    const cfg = currentConfig || {};
    const chain = (cfg.extraction && cfg.extraction.fallback_chain) || [];
    list.replaceChildren();

    if (chain.length === 0) {
      renderChainEntry(list, 0, { provider: '', model: '' });
    } else {
      chain.forEach((entry, i) => renderChainEntry(list, i, entry));
    }
    updateRemoveButtons();
  } catch (e) {
    console.error('loadExtractionChain error:', e);
  }
}

function renderChainEntry(list, index, entry) {
  try {
    const div = document.createElement('div'); const header = document.createElement('div'); const priority = document.createElement('span'); const removeBtn = document.createElement('button'); const body = document.createElement('div');
    div.className = 'chain-entry'; div.dataset.index = String(index); header.className = 'chain-entry-header'; priority.className = 'chain-priority'; priority.textContent = t('chainPriority', {n: index + 1});
    removeBtn.className = 'chain-remove-btn'; removeBtn.title = t('chainRemove'); removeBtn.textContent = '×'; removeBtn.hidden = true; removeBtn.addEventListener('click', () => removeChainEntry(index)); header.append(priority, removeBtn); body.className = 'chain-entry-body';
    const providers = (currentConfig && currentConfig.providers) || {}; const providerSelect = document.createElement('select'); providerSelect.dataset.chainProvider = String(index);
    const providerPlaceholder = document.createElement('option'); providerPlaceholder.value = ''; providerPlaceholder.textContent = t('chainSelectProvider'); providerSelect.appendChild(providerPlaceholder);
    Object.keys(providers).forEach(providerId => { const option = document.createElement('option'); option.value = providerId; option.textContent = providerId; option.selected = providerId === entry.provider; providerSelect.appendChild(option); }); providerSelect.addEventListener('change', () => onChainProviderChange(index));
    const models = entry.provider && providers[entry.provider] ? (providers[entry.provider].models || []) : []; const modelSelect = document.createElement('select'); modelSelect.dataset.chainModel = String(index); const modelPlaceholder = document.createElement('option'); modelPlaceholder.value = ''; modelPlaceholder.textContent = '--'; modelSelect.appendChild(modelPlaceholder);
    models.forEach(model => { const option = document.createElement('option'); option.value = model; option.textContent = model; option.selected = model === entry.model; modelSelect.appendChild(option); });
    if (entry.model && !models.includes(entry.model)) { const option = document.createElement('option'); option.value = entry.model; option.textContent = entry.model + ' (custom)'; option.selected = true; modelSelect.appendChild(option); }
    const addField = (labelText, select) => { const field = document.createElement('div'); const label = document.createElement('label'); field.className = 'chain-field'; label.textContent = labelText; field.append(label, select); body.appendChild(field); };
    addField(t('labelProvider'), providerSelect); addField(t('labelModel'), modelSelect); div.append(header, body); list.appendChild(div);
  } catch (e) { console.error('renderChainEntry error:', e, {index, entry}); showToast('チェーン描画エラー: ' + (e.message || e), true); }
}
function onChainProviderChange(index) {
  const providerSelect = document.querySelector(`[data-chain-provider="${index}"]`);
  const modelSelect = document.querySelector(`[data-chain-model="${index}"]`);
  if (!providerSelect || !modelSelect) return;

  const providerId = providerSelect.value;
  const providers = (currentConfig && currentConfig.providers) || {};
  const models = providerId && providers[providerId] ? (providers[providerId].models || []) : [];

  modelSelect.replaceChildren();
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '--';
  modelSelect.appendChild(placeholder);
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    modelSelect.appendChild(opt);
  });
}

function addChainEntry() {
  try {
    const list = document.getElementById('extraction-chain-list');
    if (!list) { console.error('addChainEntry: #extraction-chain-list not found'); return; }
    const currentCount = list.querySelectorAll('.chain-entry').length;
    if (currentCount >= MAX_CHAIN_ENTRIES) {
      showToast(t('chainMaxReached', {n: MAX_CHAIN_ENTRIES}), true);
      return;
    }
    renderChainEntry(list, currentCount, { provider: '', model: '' });
    updateRemoveButtons();
  } catch (e) {
    console.error('addChainEntry error:', e);
    showToast('エラー: ' + (e.message || e), true);
  }
}

function removeChainEntry(index) {
  const list = document.getElementById('extraction-chain-list');
  if (!list) return;
  const entries = list.querySelectorAll('.chain-entry');
  if (entries.length <= 1) {
    showToast(t('chainMinOne'), true);
    return;
  }
  // 全再構築でインデックスを振り直す
  list.replaceChildren();
  const chain = collectChainData();
  chain.splice(index, 1);
  chain.forEach((entry, i) => renderChainEntry(list, i, entry));
  updateRemoveButtons();
}

function updateRemoveButtons() {
  const list = document.getElementById('extraction-chain-list');
  if (!list) return;
  const entries = list.querySelectorAll('.chain-entry');
  entries.forEach(el => {
    const btn = el.querySelector('.chain-remove-btn');
    if (btn) btn.hidden = entries.length <= 1;
  });
}

function collectChainData() {
  const list = document.getElementById('extraction-chain-list');
  if (!list) return [];
  const entries = list.querySelectorAll('.chain-entry');
  const chain = [];
  entries.forEach(el => {
    const provider = el.querySelector('[data-chain-provider]')?.value || '';
    const model = el.querySelector('[data-chain-model]')?.value || '';
    if (provider && model) {
      chain.push({ provider, model });
    }
  });
  return chain;
}

async function applyExtractionChain() {
  const chain = collectChainData();

  try {
    const res = await fetch('/api/config/extraction', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fallback_chain: chain }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    showToast(t('chainSaved'));
    if (!currentConfig) currentConfig = {};
    if (!currentConfig.extraction) currentConfig.extraction = {};
    currentConfig.extraction.fallback_chain = chain;
  } catch (err) {
    showToast(t('chainSaveFailed') + ': ' + err.message, true);
  }
}