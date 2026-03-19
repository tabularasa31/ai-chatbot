# Feature: [FI-038] "Powered by Chat9" in widget footer

## Задача

Добавить футер "Powered by Chat9" в виджет. Это брендинг — показываем на чём работает чат.

## Где менять

Один файл: `frontend/components/ChatWidget.tsx`

## Что добавить

В самом низу компонента, после блока с input'ом, добавить футер:

```tsx
<div
  style={{
    textAlign: "center",
    paddingTop: "8px",
    fontSize: "11px",
    color: "#9ca3af",
  }}
>
  Powered by{" "}
  <a
    href="https://getchat9.live"
    target="_blank"
    rel="noopener noreferrer"
    style={{
      color: "#6b7280",
      textDecoration: "none",
      fontWeight: 500,
    }}
  >
    Chat9
  </a>
</div>
```

## Итоговая структура компонента

```tsx
return (
  <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: "16px", ... }}>
    
    {/* Messages area */}
    <div style={{ flex: 1, overflowY: "auto", ... }}>
      ...
    </div>

    {/* Input */}
    <div style={{ display: "flex", gap: "8px" }}>
      <input ... />
      <Button ...>Send</Button>
    </div>

    {/* Footer — ADD THIS */}
    <div style={{ textAlign: "center", paddingTop: "8px", fontSize: "11px", color: "#9ca3af" }}>
      Powered by <a href="https://getchat9.live" target="_blank" rel="noopener noreferrer" style={{ color: "#6b7280", textDecoration: "none", fontWeight: 500 }}>Chat9</a>
    </div>

  </div>
);
```

## Требования

- Ссылка открывается в новой вкладке (`target="_blank"`)
- Стиль ненавязчивый — маленький, серый, не отвлекает
- Не мешает UX — padding небольшой
- В будущем (Premium план) этот футер можно скрывать через настройку клиента

## Файл

`frontend/components/ChatWidget.tsx`
