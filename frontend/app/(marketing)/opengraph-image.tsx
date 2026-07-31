import { ImageResponse } from "next/og";

// Applies to all marketing routes as the default OG/Twitter preview image.
// Per-post blog pages override this with their own `openGraph.images`.
export const alt = "Chat9 — AI support chatbot for your docs";
export const size = {
  width: 1200,
  height: 630,
};
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background:
            "linear-gradient(135deg, #0A0A0F 0%, #11131B 55%, #1A1030 100%)",
          padding: "80px",
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "20px",
            color: "#FAF5FF",
            fontSize: 40,
            fontWeight: 700,
          }}
        >
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 16,
              background: "#E879F9",
              color: "#0A0A0F",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 34,
              fontWeight: 800,
            }}
          >
            C9
          </div>
          Chat9
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
          <div
            style={{
              color: "#FAF5FF",
              fontSize: 68,
              fontWeight: 700,
              lineHeight: 1.1,
              maxWidth: 900,
            }}
          >
            Your support mate, always on.
          </div>
          <div style={{ color: "#A9A3BA", fontSize: 34, maxWidth: 880 }}>
            Turn your docs into a 24/7 AI support agent.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            gap: "16px",
            color: "#5EC8FF",
            fontSize: 28,
          }}
        >
          <span>Works 24/7</span>
          <span style={{ color: "#3A3A52" }}>•</span>
          <span>Daily reports</span>
          <span style={{ color: "#3A3A52" }}>•</span>
          <span>Understands context</span>
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}
