/* RP Standalone — Session Setup UI */

let selectedPersonaId = null;
let selectedPersonaName = null;
let selectedStyle = null;
let personaStyleData = null;

/* ── Step 1: Persona Selection ── */

async function loadPersonas(presetPersonaId = null) {
  try {
    const res = await fetch('/api/persona/list?status=saved');
    const personas = await res.json();

    // URLパラメータでペルソナ指定がある場合 → キャラ選択画面をスキップ
    if (presetPersonaId) {
      let target = personas.find(p => p.id === presetPersonaId);
      if (!target) {
        // 指定ペルソナ不在 → 先頭のペルソナにフォールバック
        target = personas[0];
      }
      if (target) {
        selectedPersonaId = target.id;
        selectedPersonaName = target.name;
        // グリッド描画せず直接文体選択へ
        loadStyleOptions();
        return;
      }
      // ペルソナ一覧が空 → persona step を再表示して従来フローに戻す
      document.getElementById('step-persona').classList.add('active');
    }

    // 通常フロー：グリッド描画
    renderPersonaGrid(personas);
  } catch (err) {
    console.error('loadPersonas error:', err);
    document.getElementById('persona-grid').innerHTML =
      '<p style="color:var(--error);padding:20px;">読み込み失敗: ' + err.message + '</p>';
  }
}

function renderPersonaGrid(personas) {
  const grid = document.getElementById('persona-grid');
  grid.innerHTML = personas.map(p => `
    <div class="persona-card" data-id="${escapeHtml(p.id)}" tabindex="0">
      <div class="persona-name">${escapeHtml(p.name)}</div>
      <div class="persona-id">${escapeHtml(p.id)}</div>
    </div>
  `).join('');

  document.querySelectorAll('.persona-card').forEach(card => {
    card.addEventListener('click', () => selectPersona(card));
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') selectPersona(card);
    });
  });
}

function selectPersona(card) {
  document.querySelectorAll('.persona-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');

  selectedPersonaId = card.dataset.id;
  selectedPersonaName = card.querySelector('.persona-name').textContent;

  document.getElementById('next-to-style').disabled = false;
}

/* ── Step 2: Style Selection ── */

async function loadStyleOptions() {
  try {
    const res = await fetch(`/api/persona/${selectedPersonaId}/style`);
    personaStyleData = await res.json();

    const nameEl = document.getElementById('selected-persona-name');
    if (nameEl) nameEl.textContent = selectedPersonaName;
    renderStylePresets();
    showStep('style');
  } catch (err) {
    console.error('loadStyleOptions error:', err);
    alert('スタイル情報の読み込みに失敗しました: ' + err.message);
    showStep('persona');
  }
}

function renderStylePresets() {
  const listEl = document.getElementById('style-preset-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (personaStyleData.status === 'ok') {
    // プリセットがある場合
    const presets = personaStyleData.presets || [];
    presets.forEach((p, i) => {
      const label = document.createElement('label');
      label.innerHTML = `
        <input type="radio" name="style-preset" value="${p.id}" ${i === 0 ? 'checked' : ''}>
        <span>${escapeHtml(p.label)}</span>
        <small style="display:block;color:var(--text-dim);margin-top:4px;">
          ${presetDescription(p.style)}
        </small>
      `;
      listEl.appendChild(label);
    });

    // カスタムオプション
    const customLabel = document.createElement('label');
    customLabel.innerHTML = `
      <input type="radio" name="style-preset" value="custom">
      <span data-i18n="styleCustom">${t('styleCustom')}</span>
    `;
    listEl.appendChild(customLabel);

    document.querySelectorAll('input[name="style-preset"]').forEach(radio => {
      radio.addEventListener('change', onStylePresetChange);
    });

    // デフォルト値設定
    const defaultStyle = personaStyleData.default_style || {};
    const vpEl = document.getElementById('setup-viewpoint');
    const psEl = document.getElementById('setup-person');
    const nrEl = document.getElementById('setup-narration');
    if (vpEl) vpEl.value = defaultStyle.viewpoint || 'ai_character';
    if (psEl) psEl.value = defaultStyle.person || 'first';
    if (nrEl) nrEl.value = defaultStyle.narration ? 'true' : 'false';
    updatePersonToggle();

  } else if (personaStyleData.status === 'needs_confirmation') {
    // 推定結果あり
    const est = personaStyleData.estimate;
    listEl.innerHTML = `
      <div class="estimate-banner">
        <p data-i18n="styleEstimated">SOUL.md から文体を推定しました</p>
        <pre>${JSON.stringify(est, null, 2)}</pre>
        <label>
          <input type="radio" name="style-preset" value="estimated" checked>
          <span data-i18n="useEstimated">この推定を使用する</span>
        </label>
        <label>
          <input type="radio" name="style-preset" value="custom">
          <span data-i18n="styleCustom">${t('styleCustom')}</span>
        </label>
    `;
    document.querySelectorAll('input[name="style-preset"]').forEach(radio => {
      radio.addEventListener('change', onStylePresetChange);
    });
    selectedStyle = est;

  } else {
    // 手動設定必須
    listEl.innerHTML = `
      <p style="color:var(--text-muted);" data-i18n="styleNoPreset">プリセットがありません。カスタム設定で指定してください。</p>
      <input type="radio" name="style-preset" value="custom" checked style="display:none;">
    `;
    showCustomStyle();
  }
}

function presetDescription(style) {
  if (!style) return '';
  const v = { ai_character: t('optAIChar'), user_character: t('optUserChar') };
  const p = { first: t('optFirstPerson'), third: t('optThirdPerson') };
  const n = style.narration ? t('optNarrationOn') : t('optNarrationOff');
  return `[${v[style.viewpoint] || style.viewpoint}・${n}${style.narration ? '・' + p[style.person] : ''}]`;
}

function onStylePresetChange() {
  const val = document.querySelector('input[name="style-preset"]:checked').value;
  const customOpts = document.getElementById('custom-style-opts');

  if (val === 'custom') {
    if (customOpts) customOpts.style.display = 'block';
    selectedStyle = null;
  } else if (val === 'estimated') {
    if (customOpts) customOpts.style.display = 'none';
    selectedStyle = personaStyleData.estimate;
  } else {
    if (customOpts) customOpts.style.display = 'none';
    const preset = (personaStyleData.presets || []).find(p => p.id === val);
    selectedStyle = preset ? preset.style : null;
  }
}

function updatePersonToggle() {
  const narEl = document.getElementById('setup-narration');
  const personRow = document.getElementById('setup-person-row');
  const personEl = document.getElementById('setup-person');
  if (!narEl) return;
  const nar = narEl.value === 'true';
  if (personRow) personRow.style.opacity = nar ? '1' : '0.35';
  if (personEl) personEl.disabled = !nar;
}

/* ── Navigation ── */

function showStep(step) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById('step-' + step).classList.add('active');
}

function showCustomStyle() {
  const el = document.getElementById('custom-style-opts');
  if (el) el.style.display = 'block';
}

/* ── Session Start ── */

async function startSession() {
  const btn = document.getElementById('start-session-btn');
  btn.disabled = true;
  btn.textContent = t('styleStarting') || '開始中...';

  let styleOverride = selectedStyle;
  if (!styleOverride) {
    // カスタム値を使用
    const vpEl = document.getElementById('setup-viewpoint');
    const psEl = document.getElementById('setup-person');
    const nrEl = document.getElementById('setup-narration');
    styleOverride = {
      viewpoint: vpEl ? vpEl.value : 'ai_character',
      person: psEl ? psEl.value : 'first',
      narration: nrEl ? nrEl.value === 'true' : true,
    };
  }

  try {
    const res = await fetch('/api/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        persona_id: selectedPersonaId,
        style_override: styleOverride,
        memory_scope: document.getElementById('memory-scope')?.value || 'session',
      }),
    });
    const data = await res.json();
    if (data.error) {
      alert('Error: ' + data.error);
      btn.disabled = false;
      btn.textContent = t('styleStart') || 'セッション開始';
      return;
    }
    // チャット画面へ遷移
    location.href = '/chat?new=1';
  } catch (err) {
    alert('接続エラー: ' + err);
    btn.disabled = false;
    btn.textContent = t('styleStart') || 'セッション開始';
  }
}

/* ── Init ── */

function getUrlParam(name) {
  const params = new URLSearchParams(window.location.search);
  return params.get(name);
}

document.addEventListener('DOMContentLoaded', () => {
  // 言語トグル
  i18nApply();
  updateLangToggle();

  // URL パラメータでペルソナ指定 → loadPersonas の完了後に自動選択
  const presetPersona = getUrlParam('persona');
  if (presetPersona) {
    // キャラ選択画面のチラつき防止：即座に非表示化
    document.getElementById('step-persona').classList.remove('active');
  }

  // イベントリスナー（nullガード付き）
  const nextBtn = document.getElementById('next-to-style');
  const backBtn = document.getElementById('back-to-persona');
  const startBtn = document.getElementById('start-session-btn');
  const narrSelect = document.getElementById('setup-narration');

  if (nextBtn) nextBtn.addEventListener('click', loadStyleOptions);
  if (backBtn) backBtn.addEventListener('click', () => showStep('persona'));
  if (startBtn) startBtn.addEventListener('click', startSession);
  if (narrSelect) narrSelect.addEventListener('change', updatePersonToggle);

  loadPersonas(presetPersona);
});

/* ── i18n (setup extensions — merged into i18n.js I18N) ── */

Object.assign(I18N.ja, {
    langToggle: 'EN',
    setupTitle: 'セッション設定',
    stepPersonaDesc: '使用するキャラクターを選んでください',
    btnNext: '次へ：文体選択',
    btnBack: '戻る',
    styleStart: 'セッション開始',
    styleStarting: '開始中...',
    styleCustom: 'カスタム...',
    customStyleTitle: 'カスタム設定',
    customViewpoint: '語り手',
    customNarration: '地の文',
    customPerson: '人称',
    optAIChar: 'AIキャラ視点',
    optUserChar: 'ユーザーキャラ視点',
    optNarrationOn: 'あり',
    optNarrationOff: 'なし',
    optFirstPerson: '一人称',
    optThirdPerson: '三人称',
    styleEstimated: 'SOUL.md から文体を推定しました',
    useEstimated: 'この推定を使用する',
    styleNoPreset: 'プリセットがありません。カスタム設定で指定してください。',
    loadingText: '読み込み中...',
});
Object.assign(I18N.en, {
    langToggle: '日本語',
    setupTitle: 'Session Setup',
    stepPersonaDesc: 'Select a character',
    btnNext: 'Next: Style',
    btnBack: 'Back',
    styleStart: 'Start Session',
    styleStarting: 'Starting...',
    styleCustom: 'Custom...',
    customStyleTitle: 'Custom Settings',
    customViewpoint: 'Narrator',
    customNarration: 'Narration',
    customPerson: 'Person',
    optAIChar: 'AI char. view',
    optUserChar: 'User char. view',
    optNarrationOn: 'On',
    optNarrationOff: 'Off',
    optFirstPerson: 'First-person',
    optThirdPerson: 'Third-person',
    styleEstimated: 'Style estimated from SOUL.md',
    useEstimated: 'Use this estimate',
    styleNoPreset: 'No presets. Please use custom settings.',
    loadingText: 'Loading...',
});

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}