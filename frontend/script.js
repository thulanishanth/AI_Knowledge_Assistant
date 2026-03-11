/*app/assets/script.js*/
/**
 * AI Knowledge Assistant - Studio Logic
 * Handles API interaction, UI state management, and Theme Switching.
 */

// 1. Element Selectors
const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const chatViewport = document.getElementById('chat-container');
const sendBtn = document.getElementById('send-btn');
const themeToggle = document.getElementById('theme-toggle');
const themeIcon = document.getElementById('theme-icon');
const clearChatBtn = document.getElementById('clear-chat');
const body = document.body;

// 2. Theme Logic (Light/Dark Mode)
const savedTheme = localStorage.getItem('theme');
if (savedTheme === 'light') {
    body.classList.add('light-mode');
    updateThemeIcon(true);
}

themeToggle.addEventListener('click', () => {
    const isLight = body.classList.toggle('light-mode');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    updateThemeIcon(isLight);
});

function updateThemeIcon(isLight) {
    if (isLight) {
        // Sun Icon for Light Mode
        themeIcon.innerHTML = `
            <circle cx="12" cy="12" r="5"></circle>
            <line x1="12" y1="1" x2="12" y2="3"></line>
            <line x1="12" y1="21" x2="12" y2="23"></line>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
            <line x1="1" y1="12" x2="3" y2="12"></line>
            <line x1="21" y1="12" x2="23" y2="12"></line>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>`;
    } else {
        // Moon Icon for Dark Mode
        themeIcon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';
    }
}

// 3. UI Helpers: Auto-resize Textarea
userInput.addEventListener('input', function() {
    this.style.height = 'auto';
    const newHeight = Math.min(this.scrollHeight, 180);
    this.style.height = newHeight + 'px';
    this.style.overflowY = this.scrollHeight > 180 ? 'scroll' : 'hidden';
});

// 4. Message Rendering
const appendMessage = (content, type, confidence = null) => {
    const msgWrapper = document.createElement('div');
    msgWrapper.className = `msg ${type}-msg`;

    let metaContent = '';
    if (confidence !== null) {
        metaContent = `<div class="confidence-tag">Confidence: ${Math.round(confidence * 100)}%</div>`;
    }

    msgWrapper.innerHTML = `
        <div class="msg-content">
            ${content}
            ${metaContent}
        </div>
    `;

    chatViewport.appendChild(msgWrapper);
    chatViewport.scrollTo({ top: chatViewport.scrollHeight, behavior: 'smooth' });
};

// 5. Loading Animation
const showLoading = () => {
    const loadingId = 'loading-' + Date.now();
    const loadingWrapper = document.createElement('div');
    loadingWrapper.className = 'msg bot-msg';
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

// 6. Backend Communication
chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const question = userInput.value.trim();
    if (!question) return;

    appendMessage(question, 'user');
    userInput.value = '';
    userInput.style.height = 'auto';
    userInput.disabled = true;
    sendBtn.disabled = true;

    const loadingId = showLoading();

    try {
        const response = await fetch('/api/chat/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: question })
        });

        const data = await response.json();
        document.getElementById(loadingId)?.remove();

        if (response.ok) {
            appendMessage(data.answer, 'bot', data.confidence);
        } else {
            appendMessage(`⚠️ Error: ${data.detail || 'Request failed'}`, 'bot');
        }

    } catch (error) {
        console.error("API Fetch Error:", error);
        document.getElementById(loadingId)?.remove();
        appendMessage("❌ Connection failed. Ensure the server is running.", "bot");
    } finally {
        userInput.disabled = false;
        sendBtn.disabled = false;
        userInput.focus();
    }
});

// 7. Utility: Clear Chat
clearChatBtn.addEventListener('click', () => {
    if (confirm("Start a new session? This will clear current messages.")) {
        chatViewport.innerHTML = `
            <div class="msg bot-msg">
                <div class="msg-content">
                    Session cleared. How can I help you with your database today?
                </div>
            </div>
        `;
    }
});

// 8. Enter Key Listener
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.dispatchEvent(new Event('submit'));
    }
});