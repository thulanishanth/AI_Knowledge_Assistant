const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const chatContainer = document.getElementById("chat-container");
const sendBtn = document.getElementById("send-btn");
const themeToggle = document.getElementById("theme-toggle");
const themeIcon = document.getElementById("theme-icon");
const clearChatBtn = document.getElementById("clear-chat");
const historyContainer = document.getElementById("history-container");
const historyCount = document.getElementById("history-count");
const emptyState = document.getElementById("empty-state");
const composerStatus = document.getElementById("composer-status");
const body = document.body;

let userId = localStorage.getItem("ai_assistant_user_id");
if (!userId) {
  userId = `usr_${Math.random().toString(36).slice(2, 12)}`;
  localStorage.setItem("ai_assistant_user_id", userId);
}

let sessionId = null;
let lastQuestion = "";
let loadingRow = null;

function ensureMessageList() {
  let list = chatContainer.querySelector(".message-list");
  if (!list) {
    list = document.createElement("div");
    list.className = "message-list";
    chatContainer.appendChild(list);
  }
  return list;
}

function setComposerStatus(text) {
  composerStatus.textContent = text;
}

function setTheme(theme) {
  body.dataset.theme = theme;
  themeIcon.textContent = theme === "light" ? "☀" : "◐";
  localStorage.setItem("theme", theme);
}

function autoResizeInput() {
  userInput.style.height = "auto";
  userInput.style.height = `${Math.min(userInput.scrollHeight, 220)}px`;
}

function scrollToBottom() {
  chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: "smooth" });
}

function hideEmptyState() {
  emptyState.hidden = true;
}

function showEmptyState() {
  emptyState.hidden = false;
}

function clearMessages() {
  const list = chatContainer.querySelector(".message-list");
  if (list) {
    list.remove();
  }
}

function buildInlineContent(text) {
  const fragment = document.createDocumentFragment();
  const segments = String(text).split(/(`[^`]+`)/g);
  segments.forEach((segment) => {
    if (!segment) {
      return;
    }
    if (segment.startsWith("`") && segment.endsWith("`")) {
      const code = document.createElement("code");
      code.textContent = segment.slice(1, -1);
      fragment.appendChild(code);
      return;
    }
    fragment.appendChild(document.createTextNode(segment));
  });
  return fragment;
}

function renderSafeText(container, content) {
  const lines = String(content || "").split("\n");
  let paragraph = [];
  let list = null;
  let ordered = false;

  const flushParagraph = () => {
    if (!paragraph.length) {
      return;
    }
    const p = document.createElement("p");
    p.appendChild(buildInlineContent(paragraph.join(" ")));
    container.appendChild(p);
    paragraph = [];
  };

  const flushList = () => {
    list = null;
    ordered = false;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    const orderedMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    const bulletMatch = trimmed.match(/^[-*]\s+(.*)$/);

    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    if (orderedMatch || bulletMatch) {
      flushParagraph();
      const shouldBeOrdered = Boolean(orderedMatch);
      if (!list || ordered !== shouldBeOrdered) {
        flushList();
        list = document.createElement(shouldBeOrdered ? "ol" : "ul");
        ordered = shouldBeOrdered;
        container.appendChild(list);
      }
      const item = document.createElement("li");
      item.appendChild(buildInlineContent(orderedMatch ? orderedMatch[2] : bulletMatch[1]));
      list.appendChild(item);
      return;
    }

    flushList();
    paragraph.push(trimmed);
  });

  flushParagraph();
}

function renderPresentation(presentation) {
  if (!presentation || typeof presentation !== "object") {
    return null;
  }

  const block = document.createElement("div");
  block.className = "presentation-block";

  if (presentation.kind === "metric") {
    const card = document.createElement("div");
    card.className = "metric-card";

    const label = document.createElement("div");
    label.textContent = presentation.title || "Result";

    const value = document.createElement("div");
    value.className = "metric-value";
    value.textContent = presentation.value || "";

    card.append(label, value);
    block.appendChild(card);
    return block;
  }

  if (presentation.kind === "record" && Array.isArray(presentation.fields)) {
    const grid = document.createElement("div");
    grid.className = "record-grid";

    presentation.fields.forEach((field) => {
      const item = document.createElement("div");
      item.className = "record-field";

      const label = document.createElement("div");
      label.className = "field-label";
      label.textContent = field.label || "Field";

      const value = document.createElement("div");
      value.className = "field-value";
      value.textContent = field.value || "";

      item.append(label, value);
      grid.appendChild(item);
    });

    block.appendChild(grid);
    return block;
  }

  if (presentation.kind === "rows" && Array.isArray(presentation.columns) && Array.isArray(presentation.rows)) {
    const useCards =
      presentation.layout === "cards" ||
      presentation.columns.length > 5 ||
      window.innerWidth < 840;

    if (useCards) {
      const cardList = document.createElement("div");
      cardList.className = "result-cards";
      presentation.rows.forEach((row) => {
        const card = document.createElement("div");
        card.className = "result-card";
        presentation.columns.forEach((column, index) => {
          const label = document.createElement("div");
          label.className = "field-label";
          label.textContent = column;

          const value = document.createElement("div");
          value.className = "field-value";
          value.textContent = row[index] || "";

          card.append(label, value);
        });
        cardList.appendChild(card);
      });
      block.appendChild(cardList);
    } else {
      const grid = document.createElement("div");
      grid.className = "result-grid";
      grid.style.setProperty("--col-count", String(presentation.columns.length));

      presentation.columns.forEach((column) => {
        const cell = document.createElement("div");
        cell.className = "grid-cell header";
        cell.textContent = column;
        grid.appendChild(cell);
      });

      presentation.rows.forEach((row) => {
        row.forEach((value) => {
          const cell = document.createElement("div");
          cell.className = "grid-cell";
          cell.textContent = value || "";
          grid.appendChild(cell);
        });
      });

      block.appendChild(grid);
    }

    if (presentation.truncated) {
      const note = document.createElement("div");
      note.className = "meta-row";
      const chip = document.createElement("span");
      chip.className = "meta-chip";
      chip.textContent = "Showing a limited preview";
      note.appendChild(chip);
      block.appendChild(note);
    }
    return block;
  }

  if (presentation.kind === "notice") {
    const notice = document.createElement("div");
    notice.className = "record-field";

    const label = document.createElement("div");
    label.className = "field-label";
    label.textContent = presentation.title || "Notice";

    const value = document.createElement("div");
    value.className = "field-value";
    value.textContent = presentation.message || "";

    notice.append(label, value);
    block.appendChild(notice);
    return block;
  }

  return null;
}

function appendMessage({
  role,
  content,
  confidence = null,
  presentation = null,
  meta = {},
  allowRetry = false,
}) {
  hideEmptyState();
  const list = ensureMessageList();

  const row = document.createElement("article");
  row.className = `message-row ${role === "user" ? "user" : "assistant"}`;

  const card = document.createElement("div");
  card.className = "message-card";

  const header = document.createElement("div");
  header.className = "message-header";

  const roleLabel = document.createElement("div");
  roleLabel.className = "message-role";
  roleLabel.textContent = role === "user" ? "You" : "Assistant";

  const actions = document.createElement("div");
  actions.className = "message-actions";

  if (role !== "user" && allowRetry) {
    const retryButton = document.createElement("button");
    retryButton.className = "retry-button";
    retryButton.type = "button";
    retryButton.textContent = "Retry";
    retryButton.addEventListener("click", () => {
      if (lastQuestion) {
        submitQuestion(lastQuestion, true);
      }
    });
    actions.appendChild(retryButton);
  }

  if (content) {
    const copyButton = document.createElement("button");
    copyButton.className = "copy-button";
    copyButton.type = "button";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", async () => {
      await navigator.clipboard.writeText(content);
      copyButton.textContent = "Copied";
      window.setTimeout(() => {
        copyButton.textContent = "Copy";
      }, 1400);
    });
    actions.appendChild(copyButton);
  }

  header.append(roleLabel, actions);
  card.appendChild(header);

  const presentationBlock = renderPresentation(presentation);
  if (presentationBlock) {
    card.appendChild(presentationBlock);
  }

  const shouldRenderText = Boolean(content) && presentation?.kind !== "notice";

  if (shouldRenderText) {
    const text = document.createElement("div");
    text.className = "message-text";
    renderSafeText(text, content);
    card.appendChild(text);
  }

  const metaRow = document.createElement("div");
  metaRow.className = "meta-row";
  let hasMeta = false;

  if (Number.isFinite(confidence)) {
    const chip = document.createElement("span");
    chip.className = "meta-chip";
    chip.textContent = `Confidence ${Math.round(confidence * 100)}%`;
    metaRow.appendChild(chip);
    hasMeta = true;
  }

  if (meta && meta.cached) {
    const chip = document.createElement("span");
    chip.className = "meta-chip";
    chip.textContent = "Cached result";
    metaRow.appendChild(chip);
    hasMeta = true;
  }

  if (meta && meta.strategy) {
    const chip = document.createElement("span");
    chip.className = "meta-chip";
    chip.textContent = `Strategy ${meta.strategy.replaceAll("_", " ")}`;
    metaRow.appendChild(chip);
    hasMeta = true;
  }

  if (hasMeta) {
    card.appendChild(metaRow);
  }

  row.appendChild(card);
  list.appendChild(row);
  scrollToBottom();
}

function showLoading() {
  hideEmptyState();
  const list = ensureMessageList();
  const row = document.createElement("div");
  row.className = "message-row assistant loading-row";

  const card = document.createElement("div");
  card.className = "message-card";
  ["60%", "90%", "74%"].forEach((width) => {
    const bar = document.createElement("div");
    bar.className = "loading-bar";
    bar.style.width = width;
    card.appendChild(bar);
  });

  row.appendChild(card);
  list.appendChild(row);
  loadingRow = row;
  scrollToBottom();
}

function hideLoading() {
  if (loadingRow) {
    loadingRow.remove();
    loadingRow = null;
  }
}

function markActiveHistoryItem(activeSessionId) {
  historyContainer.querySelectorAll(".history-item").forEach((item, index) => {
    const isCurrentSessionButton = index === 0 && !item.dataset.sessionId;
    item.classList.toggle(
      "active",
      isCurrentSessionButton ? !activeSessionId : item.dataset.sessionId === activeSessionId,
    );
  });
}

function renderHistory(history) {
  historyContainer.replaceChildren();

  const current = document.createElement("button");
  current.className = `history-item${sessionId ? "" : " active"}`;
  current.type = "button";
  current.textContent = "Current session";
  current.addEventListener("click", () => resetSession());
  historyContainer.appendChild(current);

  if (!history.length) {
    const empty = document.createElement("div");
    empty.className = "history-item empty";
    empty.textContent = "No saved sessions yet";
    historyContainer.appendChild(empty);
    historyCount.textContent = "0";
    return;
  }

  history.forEach((item) => {
    const button = document.createElement("button");
    button.className = `history-item${sessionId === item.session_id ? " active" : ""}`;
    button.type = "button";
    button.dataset.sessionId = item.session_id;
    button.textContent = item.title || "Untitled session";
    button.addEventListener("click", () => loadSessionData(item.session_id));
    historyContainer.appendChild(button);
  });

  historyCount.textContent = String(history.length);
}

async function loadHistory() {
  try {
    const response = await fetch(`/api/chat/history/${userId}`);
    const data = await response.json();
    renderHistory(Array.isArray(data.history) ? data.history : []);
  } catch (error) {
    console.error("Failed to load history", error);
    renderHistory([]);
  }
}

async function loadSessionData(targetSessionId) {
  if (!targetSessionId) {
    return;
  }

  sessionId = targetSessionId;
  markActiveHistoryItem(targetSessionId);
  clearMessages();
  showLoading();
  setComposerStatus("Loading session...");

  try {
    const response = await fetch(`/api/chat/session/${targetSessionId}`);
    const data = await response.json();
    hideLoading();

    if (!response.ok) {
      appendMessage({
        role: "assistant",
        content: data.detail || "Failed to load the selected session.",
        allowRetry: false,
      });
      return;
    }

    clearMessages();
    if (!Array.isArray(data.messages) || !data.messages.length) {
      showEmptyState();
      return;
    }

    data.messages.forEach((message) => {
      appendMessage({
        role: message.role === "user" ? "user" : "assistant",
        content: message.content,
        confidence: message.confidence,
      });
    });
  } catch (error) {
    hideLoading();
    appendMessage({
      role: "assistant",
      content: "Failed to load conversation history.",
      allowRetry: false,
    });
    console.error("Failed to load session", error);
  } finally {
    setComposerStatus("Ready");
  }
}

async function submitQuestion(question, isRetry = false) {
  const cleanedQuestion = String(question || "").trim();
  if (!cleanedQuestion) {
    return;
  }

  lastQuestion = cleanedQuestion;
  if (!isRetry) {
    appendMessage({ role: "user", content: cleanedQuestion });
  }

  userInput.value = "";
  autoResizeInput();
  userInput.disabled = true;
  sendBtn.disabled = true;
  setComposerStatus("Thinking...");
  showLoading();

  try {
    const response = await fetch("/api/chat/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: cleanedQuestion,
        user_id: userId,
        session_id: sessionId,
      }),
    });

    const data = await response.json().catch(() => ({}));
    hideLoading();

    if (!response.ok) {
      appendMessage({
        role: "assistant",
        content: data.detail || `Request failed (${response.status})`,
        allowRetry: true,
      });
      return;
    }

    const newSession = !sessionId && data.session_id;
    sessionId = data.session_id || sessionId;

    appendMessage({
      role: "assistant",
      content: data.answer || "No answer generated.",
      confidence: data.confidence,
      presentation: data.presentation,
      meta: data.meta || {},
    });

    if (newSession) {
      await loadHistory();
      markActiveHistoryItem(sessionId);
    }
  } catch (error) {
    hideLoading();
    appendMessage({
      role: "assistant",
      content: "Connection failed. Ensure the server is running.",
      allowRetry: true,
    });
    console.error("Chat request failed", error);
  } finally {
    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
    setComposerStatus("Ready");
  }
}

function resetSession() {
  sessionId = null;
  clearMessages();
  showEmptyState();
  markActiveHistoryItem(null);
  setComposerStatus("Ready");
}

themeToggle.addEventListener("click", () => {
  setTheme(body.dataset.theme === "light" ? "dark" : "light");
});

userInput.addEventListener("input", autoResizeInput);

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitQuestion(userInput.value);
});

clearChatBtn.addEventListener("click", () => {
  resetSession();
});

document.querySelectorAll(".suggestion-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    userInput.value = chip.textContent || "";
    autoResizeInput();
    userInput.focus();
  });
});

document.addEventListener("DOMContentLoaded", async () => {
  setTheme(localStorage.getItem("theme") || "dark");
  autoResizeInput();
  await loadHistory();
  showEmptyState();
});
