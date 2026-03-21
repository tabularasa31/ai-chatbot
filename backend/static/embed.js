(function () {
  const currentScript =
    document.currentScript ||
    (() => {
      const scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();

  const scriptSrc = currentScript.src;
  const url = new URL(scriptSrc);
  const clientId = url.searchParams.get("clientId");

  if (!clientId) {
    console.error("Chat9: clientId not found in script URL");
    return;
  }

  const widgetBase =
    (typeof window !== "undefined" && window.Chat9Config?.widgetUrl) || url.origin;
  const container = document.createElement("div");
  container.id = "chat9-widget-container";
  container.style.cssText = `
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 9999;
  `;
  document.body.appendChild(container);

  const browserLocale =
    (typeof navigator !== "undefined" &&
      (navigator.language || navigator.userLanguage)) ||
    null;
  const iframe = document.createElement("iframe");
  const widgetParams = new URLSearchParams({ clientId });
  if (browserLocale) widgetParams.set("locale", browserLocale);
  iframe.src = `${widgetBase}/widget?${widgetParams.toString()}`;
  iframe.id = "chat9-widget-iframe";
  iframe.style.cssText = `
    width: 400px;
    height: 600px;
    border: none;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  `;
  iframe.allow = "microphone; camera";

  container.appendChild(iframe);

  console.log("Chat9 Widget loaded", { clientId });
})();
