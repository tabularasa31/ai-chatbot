(function() {
  "use strict";

  var scriptEl = document.currentScript;
  var apiBase = scriptEl ? new URL(scriptEl.src).origin : "https://ai-chatbot-production-6531.up.railway.app";

  function init() {
    var container = document.getElementById("ai-chat-widget");
    if (!container) return;

    var apiKey = container.getAttribute("data-api-key");
    if (!apiKey) return;

    var sessionId = null;

    var styles = document.createElement("style");
  styles.textContent = [
    "#ai-chat-widget-btn{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:#2563eb;border:none;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,0.15);z-index:9999;display:flex;align-items:center;justify-content:center;transition:transform 0.2s}",
    "#ai-chat-widget-btn:hover{transform:scale(1.05)}",
    "#ai-chat-widget-panel{position:fixed;bottom:90px;right:24px;width:380px;height:500px;background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.12);z-index:9998;display:none;flex-direction:column;overflow:hidden}",
    "#ai-chat-widget-panel.open{display:flex}",
    "#ai-chat-widget-header{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid #e5e7eb;background:#f9fafb}",
    "#ai-chat-widget-header h3{margin:0;font-size:16px;font-weight:600;color:#111827}",
    "#ai-chat-widget-close{background:none;border:none;cursor:pointer;padding:4px;color:#6b7280;font-size:20px;line-height:1}",
    "#ai-chat-widget-close:hover{color:#111827}",
    "#ai-chat-widget-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}",
    ".ai-chat-msg{max-width:85%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.5}",
    ".ai-chat-msg.user{align-self:flex-end;background:#2563eb;color:#fff}",
    ".ai-chat-msg.assistant{align-self:flex-start;background:#f3f4f6;color:#111827}",
    ".ai-chat-msg.loading{align-self:flex-start;background:#f3f4f6;color:#6b7280}",
    "#ai-chat-widget-input-area{display:flex;gap:8px;padding:16px;border-top:1px solid #e5e7eb}",
    "#ai-chat-widget-input{flex:1;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;outline:none}",
    "#ai-chat-widget-input:focus{border-color:#2563eb}",
    "#ai-chat-widget-send{padding:10px 16px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500}",
    "#ai-chat-widget-send:hover{background:#1d4ed8}",
    "#ai-chat-widget-send:disabled{background:#9ca3af;cursor:not-allowed}"
  ].join("");
  document.head.appendChild(styles);

  var chatIcon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
  var closeIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

  var btn = document.createElement("button");
  btn.id = "ai-chat-widget-btn";
  btn.innerHTML = chatIcon;
  btn.setAttribute("aria-label", "Open chat");

  var panel = document.createElement("div");
  panel.id = "ai-chat-widget-panel";

  var header = document.createElement("div");
  header.id = "ai-chat-widget-header";
  header.innerHTML = '<h3>AI Assistant</h3>';
  var closeBtn = document.createElement("button");
  closeBtn.id = "ai-chat-widget-close";
  closeBtn.innerHTML = closeIcon;
  closeBtn.setAttribute("aria-label", "Close");
  header.appendChild(closeBtn);

  var messagesEl = document.createElement("div");
  messagesEl.id = "ai-chat-widget-messages";

  var inputArea = document.createElement("div");
  inputArea.id = "ai-chat-widget-input-area";
  var input = document.createElement("input");
  input.id = "ai-chat-widget-input";
  input.type = "text";
  input.placeholder = "Type your message...";
  var sendBtn = document.createElement("button");
  sendBtn.id = "ai-chat-widget-send";
  sendBtn.textContent = "Send";
  inputArea.appendChild(input);
  inputArea.appendChild(sendBtn);

  panel.appendChild(header);
  panel.appendChild(messagesEl);
  panel.appendChild(inputArea);
  document.body.appendChild(btn);
  document.body.appendChild(panel);

  function togglePanel() {
    panel.classList.toggle("open");
  }

  function addMessage(content, role, isLoading) {
    var div = document.createElement("div");
    div.className = "ai-chat-msg " + (role || "assistant") + (isLoading ? " loading" : "");
    div.textContent = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function sendMessage() {
    var text = input.value.trim();
    if (!text) return;

    input.value = "";
    addMessage(text, "user");

    var loadingEl = document.createElement("div");
    loadingEl.className = "ai-chat-msg assistant loading";
    loadingEl.textContent = "...";
    messagesEl.appendChild(loadingEl);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    sendBtn.disabled = true;

    var body = { question: text };
    if (sessionId) body.session_id = sessionId;

    fetch(apiBase + "/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey
      },
      body: JSON.stringify(body)
    })
      .then(function(res) {
        if (!res.ok) {
          if (res.status === 401) throw new Error("Invalid API key");
          if (res.status === 503) throw new Error("Service unavailable");
          throw new Error("Request failed");
        }
        return res.json();
      })
      .then(function(data) {
        loadingEl.remove();
        sessionId = data.session_id;
        addMessage(data.answer, "assistant");
      })
      .catch(function(err) {
        loadingEl.remove();
        addMessage("Error: " + (err.message || "Something went wrong"), "assistant");
      })
      .finally(function() {
        sendBtn.disabled = false;
      });
  }

  btn.addEventListener("click", togglePanel);
  closeBtn.addEventListener("click", togglePanel);
  sendBtn.addEventListener("click", sendMessage);
  input.addEventListener("keydown", function(e) {
    if (e.key === "Enter") sendMessage();
  });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
