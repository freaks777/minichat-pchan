/* RP Standalone — Settings UI */

let currentConfig = null;
let originalConfig = null;

/* ── Init ── */

document.addEventListener('DOMContentLoaded', async () => {
  i18nApply();
  updateLangToggle();

  // タブ切替
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  await loadConfig();
  populateProviderSelect();
  // populateProviderSelect() で option が再構築された後にアクティブ値を設定
  document.getElementById('provider-select').value = currentConfig.active_provider || '';
  onProviderChange();
  populateWatchdogLevels();
  populatePersonaStyles();
  loadGlobalStyle();
  loadAdvancedSettings();
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
  select.innerHTML = '';
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

  modelSelect.innerHTML = '';
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

  container.innerHTML = levels.map((lv, i) => `
    <div class="escalation-level">
      <h4>${t('levelLabel', {n: i+1})} (${lv.after}秒後)</h4>
      <div class="setting-row">
        <label>${t('labelAfter')}</label>
        <input type="number" class="level-after" value="${lv.after}" min="10" max="86400" step="10">
      </div>
      <div class="setting-row">
        <label>${t('labelSubject')}</label>
        <input type="text" class="level-subject" value="${escapeHtml(lv.subject)}">
      </div>
      <div class="setting-row">
        <label>${t('labelBody')}</label>
        <textarea class="level-body" rows="3">${escapeHtml(lv.body)}</textarea>
      </div>
    </div>
  `).join('');
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
  try {
    const personasRes = await fetch('/api/persona/list');
    const personas = await personasRes.json();

    let html = '';
    for (const p of personas) {
      const styleRes = await fetch(`/api/persona/${p.id}/style`);
      const styleData = await styleRes.json();

      if (styleData.status === 'ok') {
        const presets = styleData.presets || [];
        const def = styleData.default_style || {};
        html += `
          <details class="persona-style-detail">
            <summary>${escapeHtml(p.name)} (${p.id})</summary>
            <div class="style-detail-content">
              <p><strong>${t('defaultStyle')}:</strong> ${presetDescription(def)}</p>
              <p><strong>${t('presetsLabel')}:</strong></p>
              <ul>
                ${presets.map(pr => `<li>${escapeHtml(pr.label)}: ${presetDescription(pr.style)}</li>`).join('')}
              </ul>
              <button class="btn btn-secondary btn-sm" onclick="editPersonaStyle('${p.id}')" data-i18n="btnEditInStudio">Studioで編集</button>
            </div>
          </details>
        `;
      } else {
        html += `
          <details class="persona-style-detail">
            <summary>${escapeHtml(p.name)} (${p.id}) — ${t('styleNotConfigured')}</summary>
            <div class="style-detail-content">
              <button class="btn btn-primary btn-sm" onclick="editPersonaStyle('${p.id}')" data-i18n="btnCreateInStudio">Studioで作成</button>
            </div>
          </details>
        `;
      }
    }
    container.innerHTML = html;
  } catch (err) {
    console.error('populatePersonaStyles error:', err);
    container.innerHTML = '<p style="color:var(--error);">読み込み失敗</p>';
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
}

/* ── Toast ── */

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
    labelModel: 'モデル (1行1つ)',
    labelActiveModel: 'アクティブモデル',
    hintModelFormat: 'OpenRouter系は provider/model 形式（例: openai/gpt-4o）、直接APIはモデル名のみ',
    currentProvider: '現在: ',
    btnApply: '適用',
    secApiParams: 'API 共通パラメータ',
    labelMaxTokens: 'Max Tokens',
    labelTemperature: 'Temperature',
    labelTimeout: 'Timeout (秒)',
    secWatchdog: '放置検知設定',
    labelWatchdogEnabled: '有効',
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
    labelModel: 'Models (one per line)',
    labelActiveModel: 'Active Model',
    hintModelFormat: 'OpenRouter: provider/model format. Direct API: model name only.',
    currentProvider: 'Current: ',
    btnApply: 'Apply',
    secApiParams: 'API Parameters',
    labelMaxTokens: 'Max Tokens',
    labelTemperature: 'Temperature',
    labelTimeout: 'Timeout (sec)',
    secWatchdog: 'Watchdog Settings',
    labelWatchdogEnabled: 'Enabled',
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