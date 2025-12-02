// frontend/app.js

// Frontend logic for PingPong Web Chat.
//
// Responsible for:
// - logging in with the backend and keeping the token
// - loading friend list + friend requests
// - opening a WebSocket for real-time messages
// - calling /history so each chat shows stored messages after refresh

const API_BASE = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws/chat";

// Minimal client-side state.  The server is the source of truth, but the
// browser keeps per-friend message arrays so the UI can re-render quickly
// without asking the backend on every keystroke.
let token = null;
let username = null;
let ws = null;

let activeFriend = null; // username string
let conversations = {};  // { friendUsername: [ {from, text, ts, self} ] }

const userLabel = document.getElementById("user-label");
const logoutBtn = document.getElementById("logout-btn");
const friendListEl = document.getElementById("friend-list");
const incomingRequestsEl = document.getElementById("incoming-requests");
const addFriendForm = document.getElementById("add-friend-form");
const addFriendInput = document.getElementById("add-friend-input");

const chatTitle = document.getElementById("chat-title");
const chatSubtitle = document.getElementById("chat-subtitle");
const chatMessagesEl = document.getElementById("chat-messages");
const chatInputField = document.getElementById("chat-input-field");
const chatSendBtn = document.getElementById("chat-send-btn");
const statusBar = document.getElementById("status-bar");

const uploadBtn = document.getElementById("chat-upload-btn");
const fileInput = document.getElementById("file-input");

// Reads the token that index.html stored after login.
// If it's missing, the user is sent back to the login page.
function ensureAuth() {
  token = localStorage.getItem("chat_token");
  username = localStorage.getItem("chat_username");
  if (!token || !username) {
    window.location.href = "index.html";
  }
  userLabel.textContent = `${username}`;
}

function setStatus(text) {
  statusBar.textContent = text;
}

function renderFriendList(friends) {
  friendListEl.innerHTML = "";
  friends.forEach((f) => {
    const li = document.createElement("li");
    li.className = "friend-item";
    li.textContent = f.username;
    if (activeFriend === f.username) {
      li.classList.add("active");
    }
    li.addEventListener("click", async () => {
        // When a friend is clicked we pull the latest history for that pair
        // from the backend and then render from the local conversations map.
        activeFriend = f.username;
        chatTitle.textContent = f.username;
        chatSubtitle.textContent = `Chatting with ${f.username}`;
        chatInputField.disabled = false;
        chatSendBtn.disabled = false;

        renderFriendList(friends); // refresh highlight
        await loadHistoryForFriend(f.username);
    });

    friendListEl.appendChild(li);
  });
}

function renderIncomingRequests(requests) {
  incomingRequestsEl.innerHTML = "";
  requests.forEach((req) => {
    const li = document.createElement("li");
    li.className = "request-item";
    const nameSpan = document.createElement("span");
    nameSpan.textContent = req.from_username;
    li.appendChild(nameSpan);

    const acceptBtn = document.createElement("button");
    acceptBtn.className = "btn-small btn-accept";
    acceptBtn.textContent = "Accept";
    acceptBtn.addEventListener("click", () => respondRequest(req.request_id, true));

    const rejectBtn = document.createElement("button");
    rejectBtn.className = "btn-small btn-reject";
    rejectBtn.textContent = "Reject";
    rejectBtn.addEventListener("click", () => respondRequest(req.request_id, false));

    li.appendChild(acceptBtn);
    li.appendChild(rejectBtn);
    incomingRequestsEl.appendChild(li);
  });
}

function renderConversation() {
  chatMessagesEl.innerHTML = "";

  if (!activeFriend) {
    const p = document.createElement("p");
    p.textContent = "Choose a friend on the left to start chatting.";
    p.style.color = "#b9bbbe";
    chatMessagesEl.appendChild(p);
    return;
  }

  const msgs = conversations[activeFriend] || [];
  msgs.forEach((m) => {
    const row = document.createElement("div");
    row.className = "message-row " + (m.self ? "message-self" : "message-other");

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";

    const textEl = document.createElement("div");

    if (m.kind === "file" && m.url) {
        // File message: show paperclip + filename with a link
        const link = document.createElement("a");
        link.href = m.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "📎 " + m.text;
        link.style.color = "#ffffff";
        link.style.textDecoration = "underline";
        textEl.appendChild(link);
    } else {
        // Normal text message
        textEl.textContent = m.text;
    }

    const timeEl = document.createElement("div");
    timeEl.className = "bubble-time";
    timeEl.textContent = m.ts;

    bubble.appendChild(textEl);
    bubble.appendChild(timeEl);

    row.appendChild(bubble);

    chatMessagesEl.appendChild(row);
  });

  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

async function loadFriends() {
  try {
    const res = await fetch(`${API_BASE}/friends?token=${encodeURIComponent(token)}`);
    if (!res.ok) {
      throw new Error("Failed to load friends");
    }
    const data = await res.json();
    renderFriendList(data.friends);
    renderIncomingRequests(data.incoming_requests);
  } catch (err) {
    console.error(err);
    setStatus("Error loading friend list.");
  }
}

// Calls GET /history to fetch past messages for this friend.
// The result is normalized into the same shape the live WebSocket
// messages use so the rest of the UI doesn't care where messages came from.
async function loadHistoryForFriend(friendUsername) {
  try {
    setStatus("Loading history...");
    const res = await fetch(
      `${API_BASE}/history?token=${encodeURIComponent(token)}&friend_username=${encodeURIComponent(friendUsername)}`
    );
    if (!res.ok) {
      setStatus("Failed to load history.");
      return;
    }
    const msgs = await res.json();

    conversations[friendUsername] = [];

    msgs.forEach((m) => {
      const self = m.from_username === username;
      const kind = m.kind;
      const url = m.url ? API_BASE + m.url : null;

      // Use DB timestamp instead of "now"
      const ts = new Date(m.created_at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });

      conversations[friendUsername].push({
        from: m.from_username,
        text: m.text,
        ts,
        self,
        kind,
        url,
      });
    });

    renderConversation();
    setStatus("Connected.");
  } catch (err) {
    console.error(err);
    setStatus("Error loading history.");
  }
}

async function respondRequest(requestId, accept) {
  try {
    const res = await fetch(`${API_BASE}/friends/respond?token=${encodeURIComponent(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, accept })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert("Error responding: " + (err.detail || res.statusText));
      return;
    }
    await loadFriends();
  } catch (err) {
    console.error(err);
    alert("Network error while responding to request.");
  }
}

addFriendForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const target = addFriendInput.value.trim();
  if (!target) return;
  if (target === username) {
    alert("You cannot add yourself.");
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/friends/request?token=${encodeURIComponent(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_username: target })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert("Error sending request: " + (data.detail || res.statusText));
      return;
    }
    alert("Friend request sent to " + target);
    addFriendInput.value = "";
  } catch (err) {
    console.error(err);
    alert("Network error while sending friend request.");
  }
});

logoutBtn.addEventListener("click", () => {
  localStorage.removeItem("chat_token");
  localStorage.removeItem("chat_username");
  window.location.href = "index.html";
});

chatSendBtn.addEventListener("click", sendMessage);
chatInputField.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    sendMessage();
  }
});

uploadBtn.addEventListener("click", () => {
  if (!activeFriend) {
    alert("Select a friend first.");
    return;
  }
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  uploadFile(file);
  fileInput.value = ""; // reset
});

// Calls GET /history to fetch past messages for this friend.
// The result is normalized into the same shape the live WebSocket
// messages use so the rest of the UI doesn't care where messages came from.
function addMessage(friend, from, text, self, kind = "text", url = null) {
  if (!conversations[friend]) conversations[friend] = [];
  const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  conversations[friend].push({ from, text, ts, self, kind, url });
  if (friend === activeFriend) {
    renderConversation();
  }
}

// Sends a text message over the WebSocket to the active friend.
// The UI also adds an optimistic copy locally so the chat feels instant.
function sendMessage() {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    alert("WebSocket not connected yet.");
    return;
  }
  if (!activeFriend) {
    alert("Select a friend first.");
    return;
  }
  const text = chatInputField.value.trim();
  if (!text) return;

  const msg = {
    type: "chat",
    to: activeFriend,
    text
  };
  // The WebSocket "application message": must match what the backend
  // expects in main.py (type, to, text fields).
  ws.send(JSON.stringify(msg));

  // Optimistically show our own message; server will echo too
  addMessage(activeFriend, username, text, true, "text", null);
  chatInputField.value = "";
}

// Sends the selected file over HTTP (multipart/form-data) to /upload.
// The backend responds by broadcasting a "file" message over WebSocket,
// so both sides see the same clickable link in their chat history.
async function uploadFile(file) {
  if (!activeFriend) {
    alert("Select a friend first.");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    setStatus("Uploading file...");
    const res = await fetch(
      `${API_BASE}/upload?token=${encodeURIComponent(token)}&to_username=${encodeURIComponent(activeFriend)}`,
      {
        method: "POST",
        body: formData,
      }
    );
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      alert("Upload failed: " + (data.detail || res.statusText));
      setStatus("Upload failed.");
      return;
    }

    // We don't manually add the message; server will push a "file" message
    // to both sender and receiver over WebSocket.
    setStatus("File uploaded.");
  } catch (err) {
    console.error(err);
    alert("Network error during upload.");
    setStatus("Upload error.");
  }
}

function setupWebSocket() {
  setStatus("Connecting WebSocket...");
  ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);

  ws.onopen = () => {
    setStatus("Connected.");
  };

  ws.onmessage = (event) => {
    try {
        const msg = JSON.parse(event.data);
        if (msg.type === "system") {
        setStatus(msg.message);
        } else if (msg.type === "chat") {
        const from = msg.from;
        const text = msg.text;
        if (from === username) {
            // server echo of our own message; we already displayed it
            return;
        }
        addMessage(from, from, text, false, "text", null);
        } else if (msg.type === "file") {
        const from = msg.from;
        const filename = msg.filename;
        const url = API_BASE + msg.url; // backend returned "/files/..."

        const self = from === username;
        const friend = self ? activeFriend : from;

        addMessage(friend, from, filename, self, "file", url);
        } else {
        console.log("Unknown message type:", msg);
        }
    } catch (err) {
      console.error("Error parsing WS message", err);
    }
  };

  ws.onclose = () => {
    setStatus("WebSocket disconnected.");
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    setStatus("WebSocket error.");
  };
}

function init() {
  ensureAuth();
  loadFriends();
  setupWebSocket();
  renderConversation();
}

init();
