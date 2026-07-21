/* RP Standalone — Sessions List UI */

let currentSort = 'updated'; // 'created' | 'updated'
let currentOrder = 'desc';   // 'asc' | 'desc'

async function loadSessions() {
  try {
    // /api/persona/list でペルソナ一覧を取得
    const personasRes = await fetch('/api/persona/list');
    const personas = await personasRes.json();

    // sessions/ ディレクトリから各ペルソナのセッションファイルを列挙
    // バックエンド API が必要 → まずは静的に実装、後で API 追加
    const sessionsRes = await fetch('/api/sessions/list');
    const data = await sessionsRes.json();

    renderSessions(data.sessions || []);
  } catch (err) {
    console.error('loadSessions error:', err);
    const error = document.createElement('p');
    error.className = 'load-error';
    error.textContent = '読み込み失敗: ' + err.message;
    document.getElementById('sessions-list').replaceChildren(error);
  }
}

function renderSessions(sessions) {
  const listEl = document.getElementById('sessions-list');
  const emptyEl = document.getElementById('empty-state');
  listEl.replaceChildren();

  if (!sessions || sessions.length === 0) {
    emptyEl.style.display = 'block';
    return;
  }

  emptyEl.style.display = 'none';
  const sorted = [...sessions].sort((a, b) => {
    const aVal = a[currentSort];
    const bVal = b[currentSort];
    if (aVal < bVal) return currentOrder === 'asc' ? -1 : 1;
    if (aVal > bVal) return currentOrder === 'asc' ? 1 : -1;
    return 0;
  });

  sorted.forEach(s => {
    const card = document.createElement('div');
    const main = document.createElement('div');
    const persona = document.createElement('span');
    const personaId = document.createElement('span');
    const updated = document.createElement('span');
    const meta = document.createElement('div');
    const created = document.createElement('span');
    const turns = document.createElement('span');
    const actions = document.createElement('div');
    const continueBtn = document.createElement('button');
    const editBtn = document.createElement('button');
    const newBtn = document.createElement('button');
    const deleteBtn = document.createElement('button');

    card.className = 'session-card';
    card.dataset.id = String(s.id ?? '');
    card.dataset.persona = String(s.persona_id ?? '');
    main.className = 'session-main';
    persona.className = 'session-persona';
    persona.textContent = String(s.persona_name ?? '');
    personaId.className = 'session-persona-id';
    personaId.textContent = String(s.persona_id ?? '');
    updated.className = 'session-date';
    updated.textContent = `${t('updatedLabel')} ${formatDate(s.updated)}`;
    main.append(persona, personaId, updated);

    meta.className = 'session-meta';
    created.className = 'session-created';
    created.textContent = `${t('createdLabel') || '作成:'} ${formatDate(s.created)}`;
    turns.className = 'session-turns';
    turns.textContent = `${s.turns ?? 0} ${t('turnsLabel')}`;
    meta.append(created, turns);

    actions.className = 'session-actions';
    continueBtn.className = 'btn btn-secondary btn-sm';
    continueBtn.dataset.i18n = 'btnContinue';
    continueBtn.textContent = t('btnContinue');
    continueBtn.addEventListener('click', () => continueSession(String(s.id ?? '')));
    editBtn.className = 'btn btn-secondary btn-sm';
    editBtn.dataset.i18n = 'btnEdit';
    editBtn.textContent = t('btnEdit');
    editBtn.addEventListener('click', () => editSession(String(s.id ?? '')));
    newBtn.className = 'btn btn-primary btn-sm';
    newBtn.dataset.i18n = 'btnNewWith';
    newBtn.textContent = t('btnNewWith');
    newBtn.addEventListener('click', () => startNewSession(String(s.persona_id ?? '')));
    deleteBtn.className = 'btn btn-danger btn-sm';
    deleteBtn.dataset.i18n = 'btnDelete';
    deleteBtn.textContent = t('btnDelete');
    deleteBtn.addEventListener('click', () => deleteSession(String(s.id ?? '')));
    actions.append(continueBtn, editBtn, newBtn, deleteBtn);

    card.append(main, meta, actions);
    listEl.appendChild(card);
  });
}

function formatDate(isoString) {
  if (!isoString) return '—';
  const d = new Date(isoString);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${h}:${min}`;
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

let _continueLock = false;

async function resumeSessionTo(sessionId, destination) {
  if (_continueLock) return;
  _continueLock = true;
  try {
    const res = await fetch('/api/session/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    if (data.error) {
      alert('エラー: ' + data.error);
      _continueLock = false;
      return;
    }
    location.href = destination;
  } catch (err) {
    alert('接続エラー: ' + err.message);
    _continueLock = false;
  }
}

function continueSession(sessionId) {
  return resumeSessionTo(sessionId, '/chat');
}

function editSession(sessionId) {
  return resumeSessionTo(sessionId, '/chat?edit=1');
}

function startNewSession(personaId) {
  location.href = `/setup?persona=${encodeURIComponent(personaId)}`;
}

let _deleting = false;

async function deleteSession(sessionId) {
  if (_deleting) return;
  if (!sessionId || typeof sessionId !== 'string' || !sessionId.includes('/')) {
    console.error('deleteSession: invalid sessionId', sessionId);
    alert('セッションIDが不正です。ページを再読み込みしてください。');
    return;
  }
  if (!confirm(t('confirmDelete') || 'このセッションを削除しますか？')) return;
  _deleting = true;
  try {
    const [personaId, date] = sessionId.split('/');
    const res = await fetch(`/api/sessions/${encodeURIComponent(personaId)}/${encodeURIComponent(date)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.status === 'partial' || data.status === 'error') {
      const failed = Array.isArray(data.failed_resources)
        ? data.failed_resources.join(', ')
        : 'unknown';
      throw new Error('一部の削除に失敗しました (' + failed + ')。再試行してください。');
    }
    const deletedCount = Number(data.deleted_count || 0);
    if (deletedCount > 0) {
      console.info('session delete: ' + deletedCount + ' resource(s) deleted');
    }
    await loadSessions();
  } catch (err) {
    console.error('deleteSession failed:', err);
    alert(t('deleteFailed') + ': ' + err.message);
  } finally {
    _deleting = false;
  }
}

// ソート切替
function setSort(key) {
  if (currentSort === key) {
    currentOrder = currentOrder === 'asc' ? 'desc' : 'asc';
  } else {
    currentSort = key;
    currentOrder = 'desc';
  }
  updateSortButtons();
  loadSessions();
}

function updateSortButtons() {
  document.querySelectorAll('.sort-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === currentSort);
  });
}

// 初期化
document.addEventListener('DOMContentLoaded', () => {
  updateLangToggle();
  updateSortButtons();
  i18nApply();
  document.querySelectorAll('.sort-btn').forEach(btn => {
    btn.addEventListener('click', () => setSort(btn.dataset.sort));
  });

  // 新規作成ボタン
  const newBtn = document.getElementById('new-session-btn');
  if (newBtn) {
    newBtn.addEventListener('click', () => location.href = '/setup');
  }

  loadSessions();
});

// i18n (sessions extensions — merged into i18n.js I18N)

Object.assign(I18N.ja, {
    langToggle: 'EN',
    sessionsTitle: 'セッション一覧',
    btnNewSession: '新規セッション',
    emptySessions: 'セッションがありません',
    emptyHint: '「新規セッション」から始めてみましょう',
    createdLabel: '作成: ',
    updatedLabel: '更新: ',
    turnsLabel: 'ターン',
    btnContinue: '続きから',
    btnNewWith: 'このキャラで新規',
    btnDelete: '削除',
    confirmDelete: 'このセッションを削除しますか？',
    deleteFailed: '削除失敗',
    navStudio: 'Studio',
    navSettings: '設定',
    sortUpdated: '更新順',
    sortCreated: '作成順',
});
Object.assign(I18N.en, {
    langToggle: '日本語',
    sessionsTitle: 'Sessions',
    btnNewSession: 'New Session',
    emptySessions: 'No sessions yet',
    emptyHint: 'Click "New Session" to start',
    createdLabel: 'Created: ',
    updatedLabel: 'Updated: ',
    turnsLabel: 'turns',
    btnContinue: 'Continue',
    btnNewWith: 'New with this char',
    btnDelete: 'Delete',
    confirmDelete: 'Delete this session?',
    deleteFailed: 'Delete failed',
    navStudio: 'Studio',
    navSettings: 'Settings',
    sortUpdated: 'By Updated',
    sortCreated: 'By Created',
});