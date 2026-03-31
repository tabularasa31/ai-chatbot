(function () {
  var currentScript =
    document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();

  var scriptSrc = currentScript.src;
  var url = new URL(scriptSrc);
  var clientId = url.searchParams.get("clientId");

  if (!clientId) {
    console.error("Chat9: clientId not found in script URL");
    return;
  }

  var widgetBase =
    (typeof window !== "undefined" && window.Chat9Config && window.Chat9Config.widgetUrl) ||
    url.origin;

  var browserLocale =
    (typeof navigator !== "undefined" &&
      (navigator.language || navigator.userLanguage)) ||
    null;

  var isOpen = false;
  var isResizing = false;
  var resizeStartX, resizeStartY, resizeStartW, resizeStartH;

  var CHAT_ICON =
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<path d="M21 15C21 15.5304 20.7893 16.0391 20.4142 16.4142C20.0391 16.7893 19.5304 17 19 17H7L3 21V5C3 4.46957 3.21071 3.96086 3.58579 3.58579C3.96086 3.21071 4.46957 3 5 3H19C19.5304 3 20.0391 3.21071 20.4142 3.58579C20.7893 3.96086 21 4.46957 21 5V15Z" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    "</svg>";

  var CLOSE_ICON =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<line x1="18" y1="6" x2="6" y2="18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>' +
    '<line x1="6" y1="6" x2="18" y2="18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>' +
    "</svg>";

  // ── Outer fixed container ──────────────────────────────────────────────────
  var container = document.createElement("div");
  container.id = "chat9-widget-container";
  container.style.cssText =
    "position:fixed;" +
    "bottom:20px;" +
    "right:20px;" +
    "z-index:9999;" +
    "display:flex;" +
    "flex-direction:column;" +
    "align-items:flex-end;" +
    "gap:12px;";
  document.body.appendChild(container);

  // ── Chat window ────────────────────────────────────────────────────────────
  var chatWindow = document.createElement("div");
  chatWindow.id = "chat9-chat-window";
  chatWindow.style.cssText =
    "display:none;" +
    "width:400px;" +
    "height:600px;" +
    "min-width:280px;" +
    "min-height:360px;" +
    "max-width:min(700px, calc(100vw - 40px));" +
    "max-height:min(820px, calc(100vh - 100px));" +
    "position:relative;" +
    "border-radius:16px;" +
    "overflow:hidden;" +
    "box-shadow:0 8px 40px rgba(0,0,0,0.20);" +
    "opacity:0;" +
    "transform:scale(0.93) translateY(10px);" +
    "transform-origin:bottom right;" +
    "transition:opacity 0.22s ease, transform 0.22s ease;";

  // ── Resize handle (top-left corner) ───────────────────────────────────────
  var resizeHandle = document.createElement("div");
  resizeHandle.id = "chat9-resize-handle";
  resizeHandle.style.cssText =
    "position:absolute;" +
    "top:0;" +
    "left:0;" +
    "width:28px;" +
    "height:28px;" +
    "cursor:nw-resize;" +
    "z-index:20;" +
    "display:flex;" +
    "align-items:center;" +
    "justify-content:center;" +
    "border-radius:0 0 6px 0;";
  resizeHandle.innerHTML =
    '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<circle cx="2" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    '<circle cx="6" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    '<circle cx="2" cy="6" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    "</svg>";

  // ── Iframe ─────────────────────────────────────────────────────────────────
  var iframe = document.createElement("iframe");
  var widgetParams = new URLSearchParams({ clientId: clientId });
  if (browserLocale) widgetParams.set("locale", browserLocale);
  iframe.src = widgetBase + "/widget?" + widgetParams.toString();
  iframe.id = "chat9-widget-iframe";
  iframe.style.cssText =
    "width:100%;" +
    "height:100%;" +
    "border:none;" +
    "display:block;";
  iframe.allow = "microphone; camera";

  // ── Resize overlay (blocks iframe from eating mouse events during resize) ──
  var resizeOverlay = document.createElement("div");
  resizeOverlay.style.cssText =
    "display:none;" +
    "position:fixed;" +
    "top:0;left:0;right:0;bottom:0;" +
    "z-index:10000;" +
    "cursor:nw-resize;";

  // ── FAB button ─────────────────────────────────────────────────────────────
  var fab = document.createElement("button");
  fab.id = "chat9-fab";
  fab.title = "Chat9";
  fab.style.cssText =
    "width:56px;" +
    "height:56px;" +
    "border-radius:50%;" +
    "border:none;" +
    "background:linear-gradient(135deg,#e879f9,#a855f7);" +
    "cursor:pointer;" +
    "display:flex;" +
    "align-items:center;" +
    "justify-content:center;" +
    "box-shadow:0 4px 18px rgba(168,85,247,0.45);" +
    "transition:transform 0.18s ease, box-shadow 0.18s ease;" +
    "flex-shrink:0;" +
    "outline:none;";
  fab.innerHTML = CHAT_ICON;

  fab.addEventListener("mouseenter", function () {
    fab.style.transform = "scale(1.08)";
    fab.style.boxShadow = "0 6px 24px rgba(168,85,247,0.55)";
  });
  fab.addEventListener("mouseleave", function () {
    fab.style.transform = "scale(1)";
    fab.style.boxShadow = "0 4px 18px rgba(168,85,247,0.45)";
  });

  // ── Toggle open / close ────────────────────────────────────────────────────
  function openChat() {
    isOpen = true;
    chatWindow.style.display = "block";
    requestAnimationFrame(function () {
      chatWindow.style.opacity = "1";
      chatWindow.style.transform = "scale(1) translateY(0)";
    });
    fab.innerHTML = CLOSE_ICON;
  }

  function closeChat() {
    isOpen = false;
    chatWindow.style.opacity = "0";
    chatWindow.style.transform = "scale(0.93) translateY(10px)";
    setTimeout(function () {
      chatWindow.style.display = "none";
    }, 220);
    fab.innerHTML = CHAT_ICON;
  }

  fab.addEventListener("click", function () {
    isOpen ? closeChat() : openChat();
  });

  // ── Resize logic ───────────────────────────────────────────────────────────
  resizeHandle.addEventListener("mousedown", function (e) {
    isResizing = true;
    resizeStartX = e.clientX;
    resizeStartY = e.clientY;
    resizeStartW = chatWindow.offsetWidth;
    resizeStartH = chatWindow.offsetHeight;
    resizeOverlay.style.display = "block";
    e.preventDefault();
  });

  document.addEventListener("mousemove", function (e) {
    if (!isResizing) return;
    var dx = resizeStartX - e.clientX;
    var dy = resizeStartY - e.clientY;
    var newW = Math.max(280, Math.min(700, resizeStartW + dx));
    var newH = Math.max(360, Math.min(820, resizeStartH + dy));
    chatWindow.style.width = newW + "px";
    chatWindow.style.height = newH + "px";
    e.preventDefault();
  });

  document.addEventListener("mouseup", function () {
    if (!isResizing) return;
    isResizing = false;
    resizeOverlay.style.display = "none";
  });

  // ── Assemble ───────────────────────────────────────────────────────────────
  chatWindow.appendChild(resizeHandle);
  chatWindow.appendChild(iframe);
  container.appendChild(chatWindow);
  container.appendChild(fab);
  document.body.appendChild(resizeOverlay);

  console.log("Chat9 Widget loaded", { clientId: clientId });
})();
