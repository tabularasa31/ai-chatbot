import { render } from "preact";
import "./styles.css";
import { ChatWidget } from "./ChatWidget";

const root = document.getElementById("root");
if (root) {
  render(
    <div className="flex h-screen w-full font-['Inter']">
      <ChatWidget botId="ch_POC" locale={null} identityToken={null} />
    </div>,
    root,
  );
}
