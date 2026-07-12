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
    document.getElementById('sessions-list').innerHTML = '<p style="color:var(--error);padding:20px;">読み込み失敗: ' + err.message + '</p>';
  }
}

function renderSessions(sessions) {
  const listEl = document.getElementById('sessions-list');
  const emptyEl = document.getElementById('empty-state');

  if (!sessions || sessions.length === 0) {
    listEl.innerHTML = '';
    emptyEl.style.display = 'block';
    return;
  }

  emptyEl.style.display = 'none';

  // ソート
  const sorted = [...sessions].sort((a, b) => {
    const aVal = a[currentSort];
    const bVal = b[currentSort];
    if (aVal < bVal) return currentOrder === 'asc' ? -1 : 1;
    if (aVal > bVal) return currentOrder === 'asc' ? 1 : -1;
    return 0;
  });

  listEl.innerHTML = sorted.map(s => `
    <div class="session-card" data-id="${s.id}" data-persona="${s.persona_id}">
      <div class="session-main">
        <span class="session-persona">${escapeHtml(s.persona_name)}</span>
        <span class="session-persona-id">${escapeHtml(s.persona_id)}</span>
        <span class="session-date">${t('updatedLabel')} ${formatDate(s.updated)}</span>
      </div>
      <div class="session-meta">
        <span class="session-created" data-i18n="createdLabel">作成: ${formatDate(s.created)}</span>
        <span class="session-turns">${s.turns} ${t('turnsLabel')}</span>
      </div>
      <div class="session-actions">
        <button class="btn btn-secondary btn-sm" onclick="continueSession('${s.id}')" data-i18n="btnContinue">続きから</button>
        <button class="btn btn-primary btn-sm" onclick="startNewSession('${s.persona_id}')" data-i18n="btnNewWith">このキャラで新規</button>
        <button class="btn btn-danger btn-sm" onclick="deleteSession('${s.id}')" data-i18n="btnDelete">削除</button>
      </div>
    </div>
  `).join('');
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

async function continueSession(sessionId) {
  try {
    const res = await fetch('/api/session/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    if (data.error) {
      alert('エラー: ' + data.error);
      return;
    }
    location.href = '/chat';
  } catch (err) {
    alert('接続エラー: ' + err.message);
  }
}

function startNewSession(personaId) {
  location.href = `/setup?persona=${encodeURIComponent(personaId)}`;
}

async function deleteSession(sessionId) {
  if (!sessionId || typeof sessionId !== 'string' || !sessionId.includes('/')) {
    console.error('deleteSession: invalid sessionId', sessionId);
    alert('セッションIDが不正です。ページを再読み込みしてください。');
    return;
  }
  if (!confirm(t('confirmDelete') || 'このセッションを削除しますか？')) return;
  try {
    const [personaId, date] = sessionId.split('/');
    const res = await fetch(`/api/sessions/${encodeURIComponent(personaId)}/${encodeURIComponent(date)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    loadSessions();
  } catch (err) {
    console.error('deleteSession failed:', err);
    alert(t('deleteFailed') + ': ' + err.message);
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
    notImplemented: '未実装です',
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
    notImplemented: 'Not implemented yet',
    navStudio: 'Studio',
    navSettings: 'Settings',
    sortUpdated: 'By Updated',
    sortCreated: 'By Created',
});