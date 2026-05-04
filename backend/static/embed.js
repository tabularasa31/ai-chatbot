(function () {
  var currentScript =
    document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();

  var botId = currentScript.dataset && currentScript.dataset.botId;

  if (!botId) {
    console.error("Chat9: botId not found. Set data-bot-id attribute on the script tag.");
    return;
  }

  var mode     = (currentScript.dataset && currentScript.dataset.mode)     || "bubble";
  var color    = (currentScript.dataset && currentScript.dataset.color)    || null;
  var position = (currentScript.dataset && currentScript.dataset.position) || "right";
  var targetId = (currentScript.dataset && currentScript.dataset.target)   || null;

  var widgetBase =
    (typeof window !== "undefined" && window.Chat9Config && window.Chat9Config.widgetUrl) ||
    (function () {
      var src = currentScript.src || "";
      var m = src.match(/^(https?:\/\/[^\/]+)/);
      return m ? m[1] : "";
    })();

  var browserLocale =
    (typeof navigator !== "undefined" && (navigator.language || navigator.userLanguage)) ||
    null;

  var identityToken =
    (typeof window !== "undefined" &&
      window.Chat9Config &&
      window.Chat9Config.identityToken) ||
    null;

  function buildWidgetUrl() {
    var query = "botId=" + encodeURIComponent(botId);
    if (browserLocale) query += "&locale=" + encodeURIComponent(browserLocale);
    // Stamp the embedding page's origin so the widget can postMessage us
    // back with a specific target instead of a wildcard.
    if (typeof window !== "undefined" && window.location && window.location.origin) {
      query += "&parentOrigin=" + encodeURIComponent(window.location.origin);
    }
    return widgetBase + "/widget?" + query;
  }

  function makeIframe() {
    var f = document.createElement("iframe");
    f.src = buildWidgetUrl();
    f.id = "chat9-widget-iframe";
    f.style.cssText = "width:100%;height:100%;border:none;display:block;";
    f.allow = "microphone; camera";

    // Handshake: the widget posts {type:"chat9:ready"} after it mounts and
    // registers its message listener. We respond with the identity token (or
    // an explicit no-identity sentinel for anonymous embeds) so the widget
    // never races between an anonymous greeting and a deferred identity.
    function onReady(event) {
      if (event.source !== f.contentWindow) return;
      var data = event.data;
      if (!data || typeof data !== "object" || data.type !== "chat9:ready") return;

      // Once we've handled the handshake for this iframe, drop the listener.
      // The iframe never sends a second chat9:ready, so leaving it attached
      // would just accumulate dead listeners on every re-init / HMR cycle.
      window.removeEventListener("message", onReady);

      // Refuse to post an identity token without an explicit target origin —
      // a "*" target on a payload that includes a signed token would broadcast
      // it to any document the iframe could navigate to. widgetBase is set
      // from window.Chat9Config.widgetUrl or the script src; if neither is
      // available, fail loud rather than leak.
      if (!widgetBase) {
        console.error(
          "Chat9: cannot determine widget origin — set window.Chat9Config.widgetUrl. Aborting identity handshake."
        );
        return;
      }

      if (identityToken) {
        f.contentWindow.postMessage(
          { type: "chat9:identity", identityToken: identityToken },
          widgetBase
        );
      } else {
        f.contentWindow.postMessage(
          { type: "chat9:no-identity" },
          widgetBase
        );
      }
    }
    window.addEventListener("message", onReady);
    return f;
  }

  // ── INLINE MODE ────────────────────────────────────────────────────────────
  if (mode === "inline") {
    var targetEl = targetId ? document.getElementById(targetId) : null;
    if (!targetEl) {
      console.error(
        "Chat9 inline: target element not found. " +
        "Add data-target=\"<elementId>\" to the script tag and make sure the element exists."
      );
      return;
    }
    var inlineFrame = makeIframe();
    inlineFrame.style.cssText =
      "width:100%;height:600px;border:none;display:block;" +
      "border-radius:12px;overflow:hidden;";
    targetEl.innerHTML = "";
    targetEl.appendChild(inlineFrame);
    console.log("Chat9 Widget loaded (inline)", { botId: botId });
    return;
  }

  // ── BUBBLE MODE ────────────────────────────────────────────────────────────
  var isOpen = false;
  var isResizing = false;
  var resizeStartX, resizeStartY, resizeStartW, resizeStartH;

  var fabBg = color ? color : "linear-gradient(135deg,#e879f9,#a855f7)";

  // Convert #RRGGBB → rgba string; returns null for named colors / shorthand / rgb()
  function hexToRgba(hex, a) {
    var m = hex && hex.match(/^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/);
    return m ? "rgba(" + parseInt(m[1],16) + "," + parseInt(m[2],16) + "," + parseInt(m[3],16) + "," + a + ")" : null;
  }
  var colorRgba1 = color ? hexToRgba(color, 0.40) : null;
  var colorRgba2 = color ? hexToRgba(color, 0.55) : null;
  var fabShadow  = colorRgba1 ? ("0 4px 18px " + colorRgba1) : (color ? "0 4px 18px rgba(0,0,0,0.28)" : "0 4px 18px rgba(168,85,247,0.45)");
  var fabShadowH = colorRgba2 ? ("0 6px 24px " + colorRgba2) : (color ? "0 6px 24px rgba(0,0,0,0.38)" : "0 6px 24px rgba(168,85,247,0.55)");

  var isLeft      = position === "left";
  var hEdge       = isLeft ? "left:20px;" : "right:20px;";
  var tOrigin     = isLeft ? "bottom left" : "bottom right";
  var windowAlign = isLeft ? "align-items:flex-start;" : "align-items:flex-end;";

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
    hEdge +
    "z-index:9999;" +
    "display:flex;" +
    "flex-direction:column;" +
    windowAlign +
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
    "transform-origin:" + tOrigin + ";" +
    "transition:opacity 0.22s ease, transform 0.22s ease;";

  // ── Resize handle (top corner opposite to position) ───────────────────────
  var resizeHandle = document.createElement("div");
  resizeHandle.id = "chat9-resize-handle";
  var handleCorner = isLeft ? "top:0;right:0;" : "top:0;left:0;";
  var handleCursor = isLeft ? "ne-resize" : "nw-resize";
  var handleRadius = isLeft ? "border-radius:0 0 0 6px;" : "border-radius:0 0 6px 0;";
  resizeHandle.style.cssText =
    "position:absolute;" +
    handleCorner +
    "width:28px;" +
    "height:28px;" +
    "cursor:" + handleCursor + ";" +
    "z-index:20;" +
    "display:flex;" +
    "align-items:center;" +
    "justify-content:center;" +
    handleRadius;
  resizeHandle.innerHTML =
    '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<circle cx="2" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    '<circle cx="6" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    '<circle cx="2" cy="6" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
    "</svg>";

  // ── Iframe ─────────────────────────────────────────────────────────────────
  var iframe = makeIframe();

  // ── Resize overlay ─────────────────────────────────────────────────────────
  var resizeOverlay = document.createElement("div");
  resizeOverlay.style.cssText =
    "display:none;" +
    "position:fixed;" +
    "top:0;left:0;right:0;bottom:0;" +
    "z-index:10000;" +
    "cursor:" + handleCursor + ";";

  // ── FAB button ─────────────────────────────────────────────────────────────
  var fab = document.createElement("button");
  fab.id = "chat9-fab";
  fab.title = "Chat9";
  fab.style.cssText =
    "width:56px;" +
    "height:56px;" +
    "border-radius:50%;" +
    "border:none;" +
    "background:" + fabBg + ";" +
    "cursor:pointer;" +
    "display:flex;" +
    "align-items:center;" +
    "justify-content:center;" +
    "box-shadow:" + fabShadow + ";" +
    "transition:transform 0.18s ease, box-shadow 0.18s ease;" +
    "flex-shrink:0;" +
    "outline:none;";
  fab.innerHTML = CHAT_ICON;

  fab.addEventListener("mouseenter", function () {
    fab.style.transform = "scale(1.08)";
    fab.style.boxShadow = fabShadowH;
  });
  fab.addEventListener("mouseleave", function () {
    fab.style.transform = "scale(1)";
    fab.style.boxShadow = fabShadow;
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
    setTimeout(function () { chatWindow.style.display = "none"; }, 220);
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
    var dx = isLeft ? (e.clientX - resizeStartX) : (resizeStartX - e.clientX);
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

  console.log("Chat9 Widget loaded (bubble)", { botId: botId, position: position });
})();
