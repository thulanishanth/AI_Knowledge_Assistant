import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.api import chat
from app.config import LOG_FILE, LOG_LEVEL
from app.utils.logger import clear_request_id, get_logger, set_request_id, setup_logging

setup_logging(LOG_LEVEL, LOG_FILE or None)
logger = get_logger(__name__)

app = FastAPI(
    title="AI Knowledge Assistant",
    description="An AI-powered assistant for querying databases with natural language.",
    version="1.0.0"
)

# Include API routes
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    set_request_id(request_id)
    start = time.perf_counter()
    logger.info("Incoming request %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Request completed status=%s duration_ms=%.2f",
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_id()


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Knowledge Assistant</title>
  <style>
    :root {
      --ink: #eaf3ff;
      --muted: #9cb3c8;
      --panel: rgba(7, 16, 31, 0.74);
      --panel-border: rgba(141, 180, 223, 0.28);
      --accent: #2fd3b6;
      --accent-2: #45a7ff;
      --bubble-bot: #142338;
      --bubble-user: #0f5f76;
      --input-bg: rgba(12, 24, 42, 0.9);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 20% 10%, #1d8f97 0%, transparent 36%),
        radial-gradient(circle at 80% 0%, #22468d 0%, transparent 32%),
        radial-gradient(circle at 50% 100%, #0d152f 0%, #070c17 60%, #050812 100%);
      display: grid;
      place-items: center;
      padding: 18px;
    }

    .chat-shell {
      width: min(980px, 100%);
      height: min(92vh, 860px);
      border: 1px solid var(--panel-border);
      background: var(--panel);
      border-radius: 24px;
      box-shadow: 0 30px 80px rgba(5, 9, 16, 0.65);
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
      backdrop-filter: blur(8px);
    }

    .topbar {
      padding: 18px 22px;
      border-bottom: 1px solid var(--panel-border);
      background: linear-gradient(125deg, rgba(23, 41, 67, 0.9), rgba(9, 20, 36, 0.85));
    }

    .title {
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: 0.3px;
    }

    .subtitle {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 0.9rem;
    }

    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }

    .badge {
      font-size: 0.75rem;
      color: #d3e7ff;
      background: rgba(32, 62, 98, 0.78);
      border: 1px solid var(--panel-border);
      border-radius: 999px;
      padding: 6px 10px;
    }

    #chat {
      padding: 20px;
      overflow-y: auto;
      display: grid;
      gap: 14px;
      align-content: start;
    }

    .msg {
      max-width: 84%;
      padding: 12px 15px;
      border-radius: 16px;
      border: 1px solid rgba(150, 188, 230, 0.25);
      line-height: 1.45;
      white-space: pre-wrap;
      animation: rise 0.2s ease-out;
    }

    .user {
      margin-left: auto;
      background: linear-gradient(135deg, var(--bubble-user), #17688d);
      border-color: rgba(85, 190, 245, 0.5);
    }

    .bot {
      background: linear-gradient(135deg, var(--bubble-bot), #1b2e48);
    }

    .meta {
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.76rem;
    }

    .quick-asks {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 2px 0 8px;
    }

    .quick-btn {
      border: 1px solid rgba(132, 176, 221, 0.35);
      background: rgba(14, 34, 58, 0.86);
      color: #d2e7ff;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 0.76rem;
      cursor: pointer;
    }

    .quick-btn:hover {
      border-color: #5bb1ff;
      color: #eaf6ff;
    }

    .composer {
      padding: 14px 16px 16px;
      border-top: 1px solid var(--panel-border);
      background: rgba(8, 18, 33, 0.93);
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }

    #question {
      width: 100%;
      resize: none;
      border: 1px solid rgba(124, 169, 214, 0.34);
      border-radius: 13px;
      padding: 12px;
      outline: 0;
      font-family: inherit;
      min-height: 54px;
      max-height: 140px;
      font-size: 0.95rem;
      color: var(--ink);
      background: var(--input-bg);
    }

    #question:focus {
      border-color: #79c7ff;
      box-shadow: 0 0 0 4px rgba(67, 145, 214, 0.24);
    }

    #send {
      border: 0;
      border-radius: 13px;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: #fff;
      min-width: 118px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.12s ease, filter 0.2s ease;
    }

    #send:hover {
      transform: translateY(-1px);
      filter: brightness(1.06);
    }

    #send:disabled {
      opacity: 0.62;
      cursor: not-allowed;
    }

    @keyframes rise {
      from {
        opacity: 0.4;
        transform: translateY(5px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @media (max-width: 760px) {
      .chat-shell {
        height: 94vh;
      }
      .msg {
        max-width: 94%;
      }
      .composer {
        grid-template-columns: 1fr;
      }
      #send {
        min-height: 44px;
      }
    }
  </style>
</head>
<body>
  <main class="chat-shell">
    <header class="topbar">
      <h1 class="title">AI Knowledge Assistant</h1>
      <p class="subtitle">Natural language to insights with confidence scoring and safe SQL execution.</p>
      <div class="badges">
        <span class="badge">SQL + General Answers</span>
        <span class="badge">Live Schema Context</span>
        <span class="badge">Confidence Meter</span>
      </div>
    </header>

    <section id="chat">
      <div class="msg bot">Hello. Ask your question and I will generate the best possible response.</div>
      <div class="quick-asks">
        <button class="quick-btn" data-q="Show total bookings by room type">Try: room type totals</button>
        <button class="quick-btn" data-q="List latest 10 reservations">Try: latest reservations</button>
        <button class="quick-btn" data-q="How many confirmed bookings do we have?">Try: confirmed bookings</button>
      </div>
    </section>

    <form class="composer" id="chat-form">
      <textarea id="question" placeholder="Type your question..." required></textarea>
      <button id="send" type="submit">Send</button>
    </form>
  </main>

  <script>
    const form = document.getElementById("chat-form");
    const input = document.getElementById("question");
    const chat = document.getElementById("chat");
    const send = document.getElementById("send");
    const quickButtons = document.querySelectorAll(".quick-btn");

    const addMessage = (text, kind, confidence = null) => {
      const bubble = document.createElement("div");
      bubble.className = `msg ${kind}`;
      bubble.textContent = text;
      if (confidence !== null) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = `Confidence: ${Math.round(confidence * 100)}%`;
        bubble.appendChild(meta);
      }
      chat.appendChild(bubble);
      chat.scrollTop = chat.scrollHeight;
      return bubble;
    };

    quickButtons.forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.q || "";
        input.focus();
      });
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;

      addMessage(question, "user");
      input.value = "";
      send.disabled = true;
      const loading = addMessage("Thinking...", "bot");

      try {
        const response = await fetch("/api/chat/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });

        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || `Request failed (${response.status})`);
        }

        const data = await response.json();
        loading.remove();
        addMessage(data.answer || "No answer generated.", "bot", data.confidence);
      } catch (error) {
        loading.remove();
        addMessage(`Error: ${error.message}`, "bot");
      } finally {
        send.disabled = false;
        input.focus();
      }
    });
  </script>
</body>
</html>
"""
