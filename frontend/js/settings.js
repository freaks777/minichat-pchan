/* RP Standalone — Settings UI */

let currentConfig = null;
let originalConfig = null;

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
    list.innerHTML = '';

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
    const div = document.createElement('div');
    div.className = 'chain-entry';
    div.setAttribute('data-index', index);

    const cfg = currentConfig || {};
    const providers = cfg.providers || {};
    const providerIds = Object.keys(providers);
    const providerOptions = providerIds.map(pid =>
      `<option value="${escapeHtml(pid)}" ${pid === entry.provider ? 'selected' : ''}>${escapeHtml(pid)}</option>`
    ).join('');

    const models = entry.provider && providers[entry.provider]
      ? (providers[entry.provider].models || [])
      : [];
    const modelOptions = models.map(m =>
      `<option value="${escapeHtml(m)}" ${m === entry.model ? 'selected' : ''}>${escapeHtml(m)}</option>`
    ).join('');

    div.innerHTML = `
      <div class="chain-entry-header">
        <span class="chain-priority">${t('chainPriority', {n: index + 1})}</span>
        <button class="chain-remove-btn" title="${t('chainRemove')}" style="display:none;">×</button>
      </div>
      <div class="chain-entry-body">
        <div class="chain-field">
          <label>${t('labelProvider')}</label>
          <select data-chain-provider="${index}">
            <option value="">${t('chainSelectProvider')}</option>
            ${providerOptions}
          </select>
        </div>
        <div class="chain-field">
          <label>${t('labelModel')}</label>
          <select data-chain-model="${index}">
            <option value="">--</option>
            ${modelOptions}
          </select>
        </div>
      </div>
    `;

    // provider変更イベント
    const providerSel = div.querySelector(`[data-chain-provider="${index}"]`);
    if (providerSel) {
      providerSel.addEventListener('change', () => onChainProviderChange(index));
    }

    // 削除ボタンイベント
    const removeBtn = div.querySelector('.chain-remove-btn');
    if (removeBtn) {
      removeBtn.addEventListener('click', () => removeChainEntry(index));
    }

    // カスタムモデル名対応
    const modelSelect = div.querySelector(`[data-chain-model="${index}"]`);
    if (entry.model && !models.includes(entry.model) && modelSelect) {
      const opt = document.createElement('option');
      opt.value = entry.model;
      opt.textContent = entry.model + ' (custom)';
      opt.selected = true;
      modelSelect.appendChild(opt);
    }

    list.appendChild(div);
  } catch (e) {
    console.error('renderChainEntry error:', e, {index, entry});
    showToast('チェーン描画エラー: ' + (e.message || e), true);
  }
}

function onChainProviderChange(index) {
  const providerSelect = document.querySelector(`[data-chain-provider="${index}"]`);
  const modelSelect = document.querySelector(`[data-chain-model="${index}"]`);
  if (!providerSelect || !modelSelect) return;

  const providerId = providerSelect.value;
  const providers = (currentConfig && currentConfig.providers) || {};
  const models = providerId && providers[providerId] ? (providers[providerId].models || []) : [];

  modelSelect.innerHTML = '<option value="">--</option>';
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
  list.innerHTML = '';
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
    if (btn) btn.style.display = entries.length > 1 ? 'inline-block' : 'none';
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