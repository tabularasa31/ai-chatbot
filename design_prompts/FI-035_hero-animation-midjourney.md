# FI-035: Hero Animation Prompt (Midjourney)

## Goal
Generate an animated hero visual showing Chat9 widget in action — friendly, modern, engaging.

---

## Midjourney Prompt

```
A sleek Chat9 support bot widget on a modern laptop screen, 
showing a conversation in progress. The bot is responding to a user question 
about API documentation. The interface is clean, minimalist, 
with a friendly chat bubble animation and a subtle glow effect. 
Modern SaaS aesthetic, similar to Linear or Notion. 
Color: blue and white tones. 3D perspective, right-side view. 
Realistic laptop, abstract background with soft gradients. 
Professional, approachable, tech-forward. 
--ar 16:9 --v 6 --quality 2
```

---

## Output Format

Save as: `hero-widget.png` (1920x1080 or similar)
Location: `frontend/public/images/hero-widget.png`

---

## How to Get

1. Go to Midjourney Discord or Web interface
2. Paste the prompt above
3. Let it generate ~4 options
4. Pick the best one
5. Download as PNG

---

## After Generation

- Optimize image (compress for web, use webp if supported)
- Place in `public/images/`
- In Cursor prompt, reference: `<Image src="/images/hero-widget.png" alt="Chat9 in action" />`

---

## Fallback

If you want quick result, use stock images:
- unDraw.co (vector, "customer support" or "chat")
- Pexels/Unsplash (search "SaaS dashboard")
- Then customize with overlay text or Chat9 colors
