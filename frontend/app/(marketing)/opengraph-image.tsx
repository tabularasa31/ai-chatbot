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
          <svg width="64" height="64" viewBox="5 5 22 22">
            <path
              d="M5 8a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-7l-4 3v-3a3 3 0 0 1-3-3z"
              fill="#E879F9"
            />
            <path
              d="M13 20h6a4 4 0 0 0 4-4v-4h1a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3v3l-4-3h-4a3 3 0 0 1-3-3z"
              fill="#38BDF8"
            />
          </svg>
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
