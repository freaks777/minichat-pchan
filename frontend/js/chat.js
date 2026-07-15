/* RP Standalone — Chat UI */

let currentAssistantDiv = null;  // SSE ストリーミング中の現在の応答 div
let selectedPresetId = null;
let presets = [];
let defaultStyle = {};
let personaName = "...";
let activePersonaId = "";
let activeSessionId = "";
let streaming = false;  // SSE ストリーミング中フラグ
let abortController = null;  // 中断用
let currentState = {};   // 現在の状態

function sessionParams() {
  return { persona_id: activePersonaId, session_id: activeSessionId };
}

// メッセージ編集用ローカルインデックス
let messageIndex = 0;

function isStreaming() { return streaming; }

// ストリーミング中の画面移動を防止
window.addEventListener("beforeunload", (e) => {
  if (isStreaming()) e.preventDefault();
});

function presetLabel(style) {
  if (!style) return "";
  const v = { ai_character: "AI視点", user_character: "ユーザー視点" };
  const p = { first: "一人称", third: "三人称" };
  const n = style.narration ? "地の文あり" : "地の文なし";
  return `[${v[style.viewpoint] || style.viewpoint}・${n}${style.narration ? "・" + p[style.person] : ""}]`;
}

function updatePersonToggle() {
  const nar = document.getElementById("custom-narration").value === "true";
  document.getElementById("custom-person-row").style.opacity = nar ? "1" : "0.35";
  document.getElementById("custom-person").disabled = !nar;
}

function renderPresets() {
  const listEl = document.getElementById("preset-list");
  listEl.innerHTML = "";

  presets.forEach((p, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<label>
      <input type="radio" name="preset" value="${p.id}" ${i === 0 ? "checked" : ""}>
      <span>${p.label}</span>
    </label>`;
    listEl.appendChild(li);
  });

  const customLi = document.createElement("li");
  customLi.innerHTML = `<label>
    <input type="radio" name="preset" value="custom">
    <span data-i18n="styleCustom">${t("styleCustom")}</span>
  </label>`;
  listEl.appendChild(customLi);

  document.querySelectorAll('input[name="preset"]').forEach(radio => {
    radio.addEventListener("change", onPresetChange);
  });

  document.getElementById("custom-narration").addEventListener("change", updatePersonToggle);
  document.getElementById("custom-viewpoint").value = defaultStyle.viewpoint || "ai_character";
  document.getElementById("custom-person").value = defaultStyle.person || "first";
  document.getElementById("custom-narration").value = defaultStyle.narration ? "true" : "false";
  updatePersonToggle();

  if (presets.length > 0) selectedPresetId = presets[0].id;
}

function showCustomOnly() {
  document.getElementById("preset-list").style.display = "none";
  document.getElementById("start-btn").textContent = t("styleCustomStart");
  document.getElementById("custom-opts").style.display = "block";
  document.getElementById("custom-narration").addEventListener("change", updatePersonToggle);
  updatePersonToggle();
  document.getElementById("style-panel").style.display = "flex";
}

function onPresetChange() {
  const val = document.querySelector('input[name="preset"]:checked').value;
  selectedPresetId = val;
  document.getElementById("custom-opts").style.display =
    val === "custom" ? "block" : "none";
}

async function init() {
  try {
    // セッション状態を取得
    const sessionRes = await fetch("/api/session/current");
    const sessionData = await sessionRes.json();

    if (sessionData.status !== "ok") {
      // サーバー側セッション消失 → localStorage から復元を試みる
      const saved = localStorage.getItem("rp-session");
      if (saved) {
        try {
          const s = JSON.parse(saved);
          if (s.persona_id && s.session_id) {
            const resumeRes = await fetch("/api/session/resume", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: s.persona_id + "/" + s.session_id }),
            });
            const resumeData = await resumeRes.json();
            if (!resumeData.error) {
              // 復元成功 → 再読み込みして通常フローへ
              location.reload();
              return;
            }
          }
        } catch (_) { /* 復元失敗 → セッション一覧へ */ }
      }
      // セッション未開始 → セッション一覧へ
      location.href = "/sessions";
      return;
    }

    personaName = sessionData.persona_name;
    activePersonaId = sessionData.persona_id;
    activeSessionId = sessionData.session_id || "";

    // セッション情報を永続化（ページ再読み込み時の復元用）
    localStorage.setItem("rp-session", JSON.stringify({
      persona_id: activePersonaId,
      session_id: activeSessionId,
    }));

    document.getElementById("persona-name-display").textContent =
      `${t("stylePersona")}: ${personaName}`;

    // セッション開始済み → チャットUI表示
    document.getElementById("header-style").textContent = presetLabel(sessionData.style);
    showChatUI();

  } catch (err) {
    console.error("init error:", err);
    document.getElementById("persona-name-display").textContent = t("styleLoadError");
  }
}

/* ── Session start ── */
document.addEventListener("DOMContentLoaded", () => {
  updateLangToggle();
  i18nApply();

  // 旧来のスタイル選択パネル用（セッション未開始で /chat に直接来た場合のフォールバック）
  const startBtn = document.getElementById("start-btn");
  if (startBtn) {
    startBtn.addEventListener("click", async () => {
      location.href = "/sessions";
    });
  }

  init();
});

/* ── Chat UI ── */

async function showChatUI() {
  document.getElementById("style-panel").style.display = "none";
  document.getElementById("chat-ui").style.display = "flex";
  document.getElementById("header-persona").textContent = personaName;
  document.getElementById("header-status").className = "status-dot connected";
  document.getElementById("msg-input").disabled = false;
  document.getElementById("send-btn").disabled = false;

  // モデル名を表示
  fetch("/api/config/model")
    .then(r => r.json())
    .then(d => { document.getElementById("header-model").textContent = d.model || ""; })
    .catch(() => {});

  // 履歴を読み込んで表示
  await loadHistory();

  // セッション状態を読み込み（空でもパネルを表示）
  fetch("/api/session/state")
    .then(r => r.json())
    .then(d => updateStatePanel(d.state || {}))
    .catch(() => {});

  // 状態パネルはデフォルトで表示
  document.getElementById("state-panel").style.display = "block";

  // 新規セッションなら開始状況を自動生成
  if (new URLSearchParams(location.search).get("new") === "1") {
    fetchOpening();
  }
}

async function fetchOpening() {
  try {
    const res = await fetch("/api/session/opening", { method: "POST" });
    const data = await res.json();
    if (data.opening) {
      addMessage("assistant", data.opening, false, messageIndex++);
    }
    // data.opening が null の場合は既存セッションなので何もしない
  } catch (_) { /* 失敗時は何もしない */ }
}

function toggleStatePanel() {
  const panel = document.getElementById("state-panel");
  panel.style.display = panel.style.display === "none" ? "block" : "none";
}

function updateStatePanel(state) {
  currentState = state || {};
  const content = document.getElementById("state-content");
  const entries = Object.entries(currentState);
  if (entries.length === 0) {
    content.innerHTML = '<span style="color:var(--text-dim)">' + (t("stateEmpty") || "変化なし") + '</span>';
  } else {
    const colors = {
      new: "#22c55e",
      changed: "#eab308",
      deleted: "#ef4444",
    };
    content.innerHTML = entries.map(([k, v]) => {
      const isDiff = typeof v === "object" && v !== null && "status" in v;
      const val = isDiff ? v.value : v;
      const status = isDiff ? v.status : null;
      const color = status ? (colors[status] || "var(--text-dim)") : "var(--text-dim)";
      const deco = status === "deleted" ? "text-decoration:line-through;" : "";
      return `<div style="margin-bottom:2px;color:${color};${deco}">${escapeHtml(k)}: ${escapeHtml(val)}</div>`;
    }).join("");
  }
  document.getElementById("state-toggle-btn").style.display = "inline-block";
}

async function loadHistory() {
  try {
    const params = new URLSearchParams({ persona_id: activePersonaId, session_id: activeSessionId });
    const res = await fetch("/api/session/history?" + params);
    const data = await res.json();
    messageIndex = 0;
    document.getElementById("log").innerHTML = "";
    (data.messages || []).forEach((m, i) => {
      addMessage(m.role, m.content, false, messageIndex++);
    });
    document.getElementById("msg-input").focus();
  } catch (err) {
    console.error("history load error:", err);
  }
}

function showTyping(show) {
  document.getElementById("typing-indicator").style.display = show ? "block" : "none";
}

function addMessage(role, text, isError, index) {
  const div = document.createElement("div");
  div.className = "msg " + role + (isError ? " error" : "");
  const roleLabel = role === "user" ? t("roleYou") : t("roleAssistant");
  const escaped = escapeHtml(text || "") || "...";
  const canEdit = index != null;
  let actionsHtml = "";
  if (canEdit) {
    const editBtn = '<button class="btn-edit" data-action="edit">' + t("btnEdit") + '</button>';
    const regenBtn = '<button class="btn-edit" data-action="regenerate">' + t("btnRegenerate") + '</button>';
    const delBtn = '<button class="btn-edit" data-action="delete">' + t("btnDelete") + '</button>';
    actionsHtml = '<div class="msg-actions">' + editBtn + (role === "user" ? regenBtn : "") + delBtn + '</div>';
  }
  div.innerHTML = '<div class="role">' + roleLabel + '</div>\n'
                + '<div class="text" data-index="' + (canEdit ? index : "") + '">' + escaped + '</div>\n'
                + actionsHtml;

  if (canEdit) {
    div.querySelector('[data-action="edit"]').addEventListener("click", () => startEdit(div));
    const regenBtn = div.querySelector('[data-action="regenerate"]');
    if (regenBtn) regenBtn.addEventListener("click", () => regenerate(div));
    div.querySelector('[data-action="delete"]').addEventListener("click", () => deleteMessage(div));
  }

  document.getElementById("log").appendChild(div);
  document.getElementById("log").scrollTop = document.getElementById("log").scrollHeight;
  return div;
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

function reindexMessages() {
  document.querySelectorAll("#log .msg .text[data-index]").forEach((el, i) => {
    el.dataset.index = i;
  });
}

async function startEdit(msgDiv) {
  // サーバーとDOMを同期してから編集
  await loadHistory();
  // loadHistoryでDOMが再構築されたので、編集対象を再取得
  const allMsgs = document.querySelectorAll("#log .msg");
  const idx = parseInt(msgDiv.querySelector(".text")?.dataset.index);
  if (isNaN(idx)) return;

  // 対応するメッセージをDOMから探す（loadHistory後はdata-indexが振り直されている）
  const newMsgDiv = document.querySelector(`#log .msg .text[data-index=\"${idx}\"]`)?.parentElement;
  if (!newMsgDiv) return;
  msgDiv = newMsgDiv;

  const textEl = msgDiv.querySelector(".text");
  if (!textEl || textEl.querySelector("textarea")) return;
  const orig = textEl.textContent;
  const isUser = msgDiv.classList.contains("user");

  // アクション隠す
  const actions = msgDiv.querySelector(".msg-actions");
  if (actions) actions.style.display = "none";

  const ta = document.createElement("textarea");
  ta.value = orig;
  ta.style.cssText = "width:70ch;max-width:100%;min-height:120px;font:inherit;font-size:14px;line-height:1.6;color:var(--text);background:var(--bg);border:1px solid var(--accent);border-radius:4px;padding:10px;resize:vertical;";
  textEl.textContent = "";
  textEl.appendChild(ta);
  ta.focus();

  const save = async () => {
    const newContent = ta.value;
    if (newContent === orig) { restore(); return; }
    try {
      const res = await fetch("/api/session/update-message", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx, content: newContent, ...sessionParams() }),
      });
      const d = await res.json();
      if (d.error) {
        if (d.error.includes("invalid index")) {
          await loadHistory();
          alert("表示が最新化されました。再度操作してください。");
        } else {
          alert("保存失敗: " + d.error);
        }
        restore(); return;
      }

      if (isUser) {
        // ユーザー発言編集 → 以降の履歴を削除して再生成
        textEl.textContent = newContent;
        if (actions) actions.style.display = "";

        // DOMから後続メッセージを削除
        let next = msgDiv.nextElementSibling;
        while (next) {
          const toRemove = next;
          next = next.nextElementSibling;
          toRemove.remove();
        }

        // サーバー側で truncate
        await fetch("/api/session/truncate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from_index: idx + 1, ...sessionParams() }),
        });

        // 編集後のテキストを再送信
        send(newContent);
      } else {
        // AI発言編集 → 編集済みとして表示
        textEl.textContent = newContent;
        const roleEl = msgDiv.querySelector(".role");
        if (roleEl && !roleEl.textContent.includes(t('btnEdited'))) {
          roleEl.textContent += ` ${t('btnEdited')}`;
        }
        if (actions) actions.style.display = "";
      }
    } catch (err) {
      alert("通信エラー: " + err.message); restore();
    }
  };

  const restore = () => { textEl.textContent = orig; if (actions) actions.style.display = ""; };

  ta.addEventListener("blur", save);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape") restore();
    if (e.key === "Enter" && e.ctrlKey) save();
  });
}

async function regenerate(msgDiv) {
  const textEl = msgDiv.querySelector(".text");
  const idx = parseInt(textEl?.dataset.index);
  if (isNaN(idx)) return;
  const text = textEl.textContent;

  // DOMから後続メッセージを削除
  let next = msgDiv.nextElementSibling;
  while (next) {
    const toRemove = next;
    next = next.nextElementSibling;
    toRemove.remove();
  }

  // サーバー側で truncate + 再送信
  await fetch("/api/session/truncate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_index: idx + 1, ...sessionParams() }),
  });
  send(text);
}

async function deleteMessage(msgDiv) {
  const textEl = msgDiv.querySelector(".text");
  const idx = parseInt(textEl?.dataset.index);
  if (isNaN(idx)) return;
  if (!confirm("このメッセージを削除しますか？（ユーザー発言の場合はAI応答も削除されます）")) return;

  try {
    const res = await fetch("/api/session/delete-message", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: idx, ...sessionParams() }),
    });
    const d = await res.json();
    if (d.error) {
      if (d.error.includes("invalid index")) {
        await loadHistory();
        alert("表示が最新化されました。再度操作してください。");
      } else {
        alert("削除失敗: " + d.error);
      }
      return;
    }

    // DOMを手動操作せず、サーバー状態から完全再構築（インデックスずれ防止）
    await loadHistory();
  } catch (err) {
    alert("通信エラー: " + err.message);
  }
}

/* ── Send ── */
document.addEventListener("DOMContentLoaded", () => {
  const msgInput = document.getElementById("msg-input");
  document.getElementById("send-btn").addEventListener("click", () => send());

  // ストリーミング中はナビゲーションを防止
  document.querySelectorAll("#top-nav a").forEach(link => {
    link.addEventListener("click", (e) => {
      if (isStreaming()) { e.preventDefault(); alert("応答待ちです。完了後に移動してください。"); }
    });
  });

  // 入力に応じて高さを自動調整（最大5行）
  msgInput.addEventListener("input", () => {
    msgInput.style.height = "auto";
    msgInput.style.height = Math.min(msgInput.scrollHeight, parseFloat(getComputedStyle(msgInput).lineHeight) * 5 + 20) + "px";
  });

  msgInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
});

function send(textOverride) {
  if (typeof textOverride !== "string") textOverride = null;
  const text = textOverride || (() => {
    const input = document.getElementById("msg-input");
    const t = input.value.trim();
    input.value = "";
    input.style.height = "auto";
    return t;
  })();
  if (!text) return;

  if (!textOverride) {
    addMessage("user", text, false, messageIndex++);
  }

  const input = document.getElementById("msg-input");
  input.disabled = true;
  document.getElementById("send-btn").disabled = true;
  streaming = true;
  showTyping(true);
  document.getElementById("header-status").className = "status-dot streaming";
  document.getElementById("stop-btn").style.display = "inline-block";

  // SSE ストリーミング受信
  abortController = new AbortController();
  fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, resend: !!textOverride, persona_id: activePersonaId, session_id: activeSessionId }),
    signal: abortController.signal,
  }).then(async (res) => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addMessage("assistant", "\u26a0\ufe0f " + (err.detail || err.error || "エラー"), true);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let currentDiv = addMessage("assistant", "", false, messageIndex++);
    currentAssistantDiv = currentDiv;
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = JSON.parse(line.slice(6));
          if (data.type === "chunk") {
            currentDiv.querySelector(".text").textContent += data.content;
            document.getElementById("log").scrollTop = document.getElementById("log").scrollHeight;
          } else if (data.type === "done") {
            // nothing more
          } else if (data.type === "error") {
            const msg = (data.code && t(data.code)) || data.content || t("err_api_unknown");
            currentDiv.querySelector(".text").textContent = "\u26a0\ufe0f " + msg;
          } else if (data.type === "state") {
            updateStatePanel(data.state);
          } else if (data.type === "cancelled") {
            currentDiv.querySelector(".text").textContent += "\n[中断]";
          }
        }
      }
    }
  }).catch(err => {
    if (currentAssistantDiv) {
      currentAssistantDiv.querySelector(".text").textContent = "\u26a0\ufe0f 通信エラー: " + err.message;
    } else {
      addMessage("assistant", "\u26a0\ufe0f 通信エラー: " + err.message, true);
    }
  }).finally(() => {
    streaming = false;
    abortController = null;
    currentAssistantDiv = null;
    showTyping(false);
    document.getElementById("header-status").className = "status-dot connected";
    document.getElementById("stop-btn").style.display = "none";
    input.disabled = false;
    document.getElementById("send-btn").disabled = false;
    input.focus();
  });
}

async function cancelChat() {
  if (!abortController) return;
  abortController.abort();
  try { await fetch("/api/chat/cancel", { method: "POST" }); } catch (_) {}
}
