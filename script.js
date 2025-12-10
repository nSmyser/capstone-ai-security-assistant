// script.js

let currentSessionId = null;
const sidebar = document.getElementById("sidebar");
const toggleBtn = document.getElementById("toggle-sidebar-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const sendBtn = document.getElementById("send-btn");
const chatInput = document.getElementById("chat-input");
const chatList = document.getElementById("chat-list");
const messagesDiv = document.getElementById("messages");

const ICONS = {
  pencil: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  x: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`
};

let isSending = false;

// DOM ready wiring
window.addEventListener("DOMContentLoaded", () => {
  if (toggleBtn) toggleBtn.addEventListener("click", toggleSidebar);
  if (newChatBtn) newChatBtn.addEventListener("click", handleNewChatClick);
  if (sendBtn) sendBtn.addEventListener("click", sendMessage);
  if (chatInput) {
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
  }

  // restore sidebar collapsed state
  if (localStorage.getItem("sidebar-collapsed") === "true") {
    sidebar.classList.add("collapsed");
    if (toggleBtn) toggleBtn.textContent = "›";
  } else {
    if (toggleBtn) toggleBtn.textContent = "‹";
  }

  loadSessions();
});

function toggleSidebar() {
  const collapsed = sidebar.classList.toggle("collapsed");
  localStorage.setItem("sidebar-collapsed", collapsed ? "true" : "false");
  if (toggleBtn) toggleBtn.textContent = collapsed ? "›" : "‹";
  // ensure messages area reflows
  if (messagesDiv) messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// fetch & render sessions
async function loadSessions() {
  try {
    const res = await fetch("/api/sessions");
    if (!res.ok) throw new Error("sessions fetch failed");
    const data = await res.json();

    chatList.innerHTML = "";
    if (!data.sessions || data.sessions.length === 0) {
      const created = await createSession();
      currentSessionId = created.session.id;
      return loadSessions();
    }

    data.sessions.forEach(s => {
      const li = document.createElement("li");
      li.dataset.id = s.id;
      li.className = "session-item";

      const nameSpan = document.createElement("span");
      nameSpan.className = "name";
      nameSpan.textContent = s.name;
      nameSpan.onclick = (e) => { e.stopPropagation(); openSession(s.id); };

      const actions = document.createElement("div");
      actions.style.display = "inline-flex";
      actions.style.gap = "6px";

      const renameBtn = document.createElement("button");
      renameBtn.className = "action-btn rename-session-btn";
      renameBtn.innerHTML = ICONS.pencil;
      renameBtn.title = "Rename";
      renameBtn.onclick = async (e) => {
        e.stopPropagation();
        const newName = prompt("New chat name:", s.name);
        if (!newName || newName.trim() === "") return;
        try {
          const r = await fetch(`/api/sessions/${s.id}`, {
            method: "PATCH",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({ name: newName.trim() })
          });
          if (r.ok) {
            await loadSessions();
            if (s.id === currentSessionId) loadMessages();
          } else {
            alert("Rename failed");
          }
        } catch (err) {
          console.error(err);
          alert("Rename error");
        }
      };

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "action-btn delete-session-btn";
      deleteBtn.innerHTML = ICONS.x;
      deleteBtn.title = "Delete";
      deleteBtn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this chat session? This cannot be undone.")) return;
        try {
          const r = await fetch(`/api/sessions/${s.id}`, { method: "DELETE" });
          const j = await r.json();
          if (j.deleted) {
            if (currentSessionId === s.id) {
              const listRes = await fetch("/api/sessions");
              const listData = await listRes.json();
              if (listData.sessions && listData.sessions.length > 0) currentSessionId = listData.sessions[0].id;
              else {
                const created = await createSession();
                currentSessionId = created.session.id;
              }
            }
            await loadSessions();
            await loadMessages();
          } else {
            alert("Could not delete session");
          }
        } catch (err) {
          console.error(err);
          alert("Delete failed");
        }
      };

      actions.appendChild(renameBtn);
      actions.appendChild(deleteBtn);

      li.appendChild(nameSpan);
      li.appendChild(actions);

      li.onclick = () => openSession(s.id);
      li.onkeyup = (e) => { if (e.key === "Enter") openSession(s.id); };

      if (s.id === currentSessionId) li.classList.add("active");

      chatList.appendChild(li);
    });

    // ensure an active session
    if (!currentSessionId && data.sessions.length > 0) currentSessionId = data.sessions[0].id;

    highlightActive(currentSessionId);
    if (currentSessionId) loadMessages();

  } catch (err) {
    console.error("loadSessions error", err);
  }
}

async function createSession(name = "") {
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ name })
  });
  const data = await res.json();
  return data;
}

async function handleNewChatClick() {
  try {
    const data = await createSession();
    currentSessionId = data.session.id;
    await loadSessions();
    await loadMessages();
  } catch (err) {
    console.error("new chat error", err);
    alert("Failed to create session.");
  }
}

function openSession(sessionId) {
  currentSessionId = sessionId;
  highlightActive(sessionId);
  loadMessages();
}

function highlightActive(sessionId) {
  document.querySelectorAll("#chat-list li").forEach(li => {
    li.classList.toggle("active", li.dataset.id === sessionId);
  });
}

// messages
async function loadMessages() {
  if (!currentSessionId) return;
  try {
    const res = await fetch(`/api/sessions/${currentSessionId}`);
    if (!res.ok) return;
    const data = await res.json();
    messagesDiv.innerHTML = "";
    (data.session.messages || []).forEach(m => addMessageToUI(m.role, m.content));
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  } catch (err) {
    console.error("loadMessages error", err);
  }
}

function addMessageToUI(role, text) {
  if (!messagesDiv) return;
  const wrapper = document.createElement("div");
  wrapper.className = "message-row";
  const msg = document.createElement("div");
  msg.classList.add("message");
  msg.classList.add(role === "user" ? "user-message" : "bot-message");
  msg.textContent = text;
  wrapper.appendChild(msg);
  messagesDiv.appendChild(wrapper);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function sendMessage() {
  if (isSending) return;
  const input = chatInput;
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;

  // ensure session
  if (!currentSessionId) {
    try {
      const created = await createSession();
      currentSessionId = created.session.id;
      await loadSessions();
    } catch (err) {
      console.error("session create error", err);
      alert("Unable to create session.");
      return;
    }
  }

  // add user message
  addMessageToUI("user", text);
  input.value = "";
  input.focus();

  // loading
  const wrap = document.createElement("div");
  wrap.className = "message-row";
  const loading = document.createElement("div");
  loading.className = "message loading-message";
  loading.innerHTML = `<div class="loader-dots"><span></span><span></span><span></span></div><div>Thinking…</div>`;
  wrap.appendChild(loading);
  messagesDiv.appendChild(wrap);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  // disable
  isSending = true;
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ prompt: text, session_id: currentSessionId })
    });
    const data = await res.json();
    wrap.remove();

    if (data.error) {
      addMessageToUI("assistant", "Error: " + data.error);
    } else {
      currentSessionId = data.session_id || currentSessionId;
      addMessageToUI("assistant", data.response);
      await loadSessions();
    }
  } catch (err) {
    console.error("send error", err);
    wrap.remove();
    addMessageToUI("assistant", "Error connecting to model.");
  } finally {
    isSending = false;
    if (sendBtn) sendBtn.disabled = false;
    if (messagesDiv) messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }
}
