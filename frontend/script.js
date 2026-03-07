/* =========================================
   1. VARIABLES & STATE
   ========================================= */
const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const chatViewport = document.getElementById("chat-container");
const sendBtn = document.getElementById("send-btn");
const themeToggle = document.getElementById("theme-toggle");
const themeIcon = document.getElementById("theme-icon");
const clearChatBtn = document.getElementById("clear-chat");
const body = document.body;

// Generate or retrieve a persistent User ID from local storage
let userId = localStorage.getItem("ai_assistant_user_id");
if (!userId) {
  userId = "usr_" + Math.random().toString(36).substring(2, 15);
  localStorage.setItem("ai_assistant_user_id", userId);
}

// Tracks the ACTIVE conversation. Null means a new session will be created.
let sessionId = null;

/* =========================================
   2. UI & THEME HELPER FUNCTIONS
   ========================================= */
const updateThemeIcon = (isLight) => {
  if (isLight) {
    themeIcon.innerHTML = `
            <circle cx="12" cy="12" r="5"></circle>
            <line x1="12" y1="1" x2="12" y2="3"></line>
            <line x1="12" y1="21" x2="12" y2="23"></line>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
            <line x1="1" y1="12" x2="3" y2="12"></line>
            <line x1="21" y1="12" x2="23" y2="12"></line>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
        `;
  } else {
    themeIcon.innerHTML =
      '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';
  }
};

const appendMessage = (content, type, confidence = null) => {
  const msgWrapper = document.createElement("div");
  msgWrapper.className = `msg ${type}-msg`;

  const msgContent = document.createElement("div");
  // Apply markdown styling class
  msgContent.className = "msg-content markdown-body";

  // Parse Markdown to HTML for bot messages only (prevents XSS from user input)
  if (type === "bot" && typeof window.marked !== "undefined") {
    msgContent.innerHTML = window.marked.parse(content);
  } else {
    msgContent.textContent = content;
  }

  msgWrapper.appendChild(msgContent);

  // Show confidence score for bot messages
  if (confidence !== null && Number.isFinite(confidence)) {
    const confidenceTag = document.createElement("div");
    confidenceTag.className = "confidence-tag";
    confidenceTag.textContent = `Confidence: ${Math.round(confidence * 100)}%`;
    msgContent.appendChild(confidenceTag);
  }

  chatViewport.appendChild(msgWrapper);

  // Auto-scroll to the newest message
  chatViewport.scrollTo({ top: chatViewport.scrollHeight, behavior: "smooth" });
};

const showLoading = () => {
  const loadingId = `loading-${Date.now()}`;
  const loadingWrapper = document.createElement("div");
  loadingWrapper.className = "msg bot-msg";
  loadingWrapper.id = loadingId;

  loadingWrapper.innerHTML = `
        <div class="msg-content">
            <div class="thinking-pulse">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
    `;

  chatViewport.appendChild(loadingWrapper);
  chatViewport.scrollTop = chatViewport.scrollHeight;
  return loadingId;
};

/* =========================================
   3. API FETCHING FUNCTIONS
   ========================================= */
async function loadHistory() {
  try {
    const response = await fetch(`/api/chat/history/${userId}`);
    const data = await response.json();

    const historyContainer = document.getElementById("history-container");
    historyContainer.innerHTML = `
            <div class="history-label">Recent History</div>
            <div class="history-item active" id="current-session-btn">Current Session</div>
        `;

    document
      .getElementById("current-session-btn")
      .addEventListener("click", () => {
        clearChatBtn.click();
      });

    if (data.history && data.history.length > 0) {
      data.history.forEach((item) => {
        if (!item.session_id) return;

        const div = document.createElement("div");
        div.className = "history-item";

        const displayTitle =
          item.title.length > 25
            ? item.title.substring(0, 25) + "..."
            : item.title;

        div.textContent = displayTitle;
        div.title = item.title;
        div.dataset.sessionId = item.session_id;

        div.addEventListener("click", function () {
          document
            .querySelectorAll(".history-item")
            .forEach((el) => el.classList.remove("active"));
          this.classList.add("active");
          loadSessionData(item.session_id);
        });

        historyContainer.appendChild(div);
      });
    }
  } catch (error) {
    console.error("Failed to load history:", error);
  }
}

async function loadSessionData(sid) {
  if (!sid) return;

  sessionId = sid;
  chatViewport.innerHTML = "";
  const loadingId = showLoading();

  try {
    const response = await fetch(`/api/chat/session/${sid}`);
    const data = await response.json();

    if (!response.ok) {
      appendMessage(
        `Error: Could not load session (${response.status})`,
        "bot",
      );
      return;
    }

    if (data.messages && data.messages.length > 0) {
      data.messages.forEach((msg) => {
        appendMessage(msg.content, msg.role, msg.confidence);
      });
    } else {
      appendMessage("Session loaded, but no messages were found.", "bot");
    }
  } catch (error) {
    appendMessage(
      "Failed to load conversation history. Check your connection.",
      "bot",
    );
    console.error("Session load error:", error);
  } finally {
    // Guaranteed removal of loading dots regardless of success or failure
    document.getElementById(loadingId)?.remove();
  }
}

/* =========================================
   4. EVENT LISTENERS
   ========================================= */
const savedTheme = localStorage.getItem("theme");
if (savedTheme === "light") {
  body.classList.add("light-mode");
  updateThemeIcon(true);
}
themeToggle.addEventListener("click", () => {
  const isLight = body.classList.toggle("light-mode");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  updateThemeIcon(isLight);
});

userInput.addEventListener("input", function () {
  this.style.height = "auto";
  const newHeight = Math.min(this.scrollHeight, 180);
  this.style.height = `${newHeight}px`;
  this.style.overflowY = this.scrollHeight > 180 ? "scroll" : "hidden";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = userInput.value.trim();
  if (!question) return;

  appendMessage(question, "user");

  userInput.value = "";
  userInput.style.height = "auto";
  userInput.disabled = true;
  sendBtn.disabled = true;

  const loadingId = showLoading();

  try {
    const response = await fetch("/api/chat/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        user_id: userId,
        session_id: sessionId,
      }),
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      appendMessage(
        `Error: ${data.detail || `Request failed (${response.status})`}`,
        "bot",
      );
      return;
    }

    const isNewSession = !sessionId && data.session_id;
    sessionId = data.session_id || sessionId;

    appendMessage(
      data.answer || "No answer generated.",
      "bot",
      data.confidence,
    );

    if (isNewSession) {
      loadHistory();
    }
  } catch (error) {
    appendMessage("Connection failed. Ensure the server is running.", "bot");
    console.error("API fetch error:", error);
  } finally {
    document.getElementById(loadingId)?.remove();
    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  }
});

clearChatBtn.addEventListener("click", () => {
  if (confirm("Start a new session? This will clear current messages.")) {
    sessionId = null;

    chatViewport.innerHTML = `
            <div class="msg bot-msg">
                <div class="msg-content markdown-body">
                    Session cleared. How can I help you with your database today?
                </div>
            </div>
        `;

    document
      .querySelectorAll(".history-item")
      .forEach((el) => el.classList.remove("active"));
    const currentSessionBtn = document.getElementById("current-session-btn");
    if (currentSessionBtn) currentSessionBtn.classList.add("active");
  }
});

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.dispatchEvent(new Event("submit"));
  }
});

/* =========================================
   5. INITIALIZATION
   ========================================= */
document.addEventListener("DOMContentLoaded", loadHistory);
