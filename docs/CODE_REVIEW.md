# Ревью кода ai-chatbot

## 1. Middleware — незащищённый маршрут `/review`

```4:5:frontend/middleware.ts
const PROTECTED_PATHS = ["/dashboard", "/documents", "/logs", "/debug", "/admin"];
const AUTH_PATHS = ["/login", "/signup"];
```

`/review` не входит в `PROTECTED_PATHS` и не указан в `matcher`. Middleware для `/review` не выполняется, страница доступна без авторизации. API вернёт 401, но страница отрисуется.

**Рекомендация:** добавить `/review` в `PROTECTED_PATHS` и в `matcher`.

---

## 2. Неиспользуемые UI-компоненты

- **`Button`** и **`Card`** (`frontend/components/ui/`) нигде не импортируются. В Hero, CTABanner, Navigation, DemoBlock и т.д. используются обычные `<button>`.
- **`use-mobile.ts`** не используется в проекте.

Компоненты и хук остаются мёртвым кодом.

---

## 3. Дублирование логики проверки токена

В `login/page.tsx` и `signup/page.tsx`:

```15:20:frontend/app/(auth)/login/page.tsx
  useEffect(() => {
    const token = getToken();
    if (token) {
      saveToken(token);
      router.replace("/dashboard");
    }
  }, [router]);
```

`saveToken(token)` при уже существующем токене ничего не меняет. Логика повторяется в нескольких местах.

---

## 4. Landing page — лишняя проверка токена

```18:25:frontend/app/(marketing)/page.tsx
  useEffect(() => {
    const token = getToken();
    if (token) {
      saveToken(token);
      router.replace('/dashboard');
    } else {
      setChecked(true);
    }
  }, [router]);
```

`saveToken(token)` при редиректе не нужен — токен уже в `localStorage`. Достаточно проверки и редиректа.

---

## 5. Backend — устаревший `datetime.utcnow()`

```44:45:backend/auth/routes.py
    user.verification_expires_at = datetime.utcnow() + timedelta(days=2)
```

`datetime.utcnow()` помечен как deprecated в Python 3.12+. Рекомендуется `datetime.now(timezone.utc)`.

---

## 6. CORS — `allow_credentials=False` при использовании cookies

```26:31:backend/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Токен передаётся через cookie. При `allow_credentials=False` браузер не отправляет cookies в cross-origin запросах. Если фронт и бэк на разных доменах, авторизация по cookie может не работать.

---

## 7. Хардкод GitHub-ссылок

```31:34:frontend/components/marketing/Navigation.tsx
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
```

Ссылка ведёт на `https://github.com` вместо репозитория проекта.

---

## 8. Кнопки CTA без навигации

В Hero, CTABanner, Navigation кнопки «Try for free» и «See demo» не имеют `onClick`/`href` и не ведут на signup или demo.

---

## 9. `useScrollAnimation` — возможная рассинхронизация ref

```9:11:frontend/components/hooks/useScrollAnimation.ts
export function useScrollAnimation(): [
  React.RefObject<HTMLDivElement>,
  boolean,
] {
  const ref = useRef<HTMLDivElement | null>(null)
```

Возвращается `ref as React.RefObject<HTMLDivElement>`, хотя `ref.current` может быть `null`. Для framer-motion это обычно допустимо, но типизация может вводить в заблуждение.

---

## 10. `useIsMobile` — SSR и гидрация

```10:17:frontend/components/ui/use-mobile.ts
  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };
    mql.addEventListener("change", onChange);
    setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    return () => mql.removeEventListener("change", onChange);
  }, []);
```

При SSR `isMobile` будет `undefined`, затем станет `true`/`false` после гидрации. Может вызвать мерцание или рассинхрон, если компонент от этого зависит.

---

## 11. `api.auth.getMe` — несоответствие типов

```151:152:frontend/lib/api.ts
      return data as { id: string; email: string; created_at: string };
```

В бэкенде `UserResponse.id` — `uuid.UUID`, сериализуется как строка. Тип `string` корректен, но `created_at` приходит как ISO-строка, а не `Date`.

---

## 12. Документы — двойной вызов `load()`

```56:59:frontend/app/(app)/documents/page.tsx
      const doc = await api.documents.upload(file);
      setDocuments((prev) => [doc, ...prev]);
      await api.embeddings.create(doc.id);
      await load();
```

После `setDocuments` вызывается `load()`, который перезаписывает список. Первое обновление состояния избыточно.

---

## 13. Отсутствие обработки ошибок при `navigator.clipboard`

```96:99:frontend/app/(app)/dashboard/page.tsx
  function copyApiKey() {
    if (apiKey) {
      navigator.clipboard.writeText(apiKey);
      setCopiedApiKey(true);
```

`navigator.clipboard.writeText` может выбросить исключение (например, без HTTPS или при отказе пользователя). Ошибка не обрабатывается.

---

## 14. `Features` — использование `index` как `key`

```57:64:frontend/components/marketing/Features.tsx
        {features.map((feature, index) => (
          <FeatureCard
            key={index}
            icon={feature.icon}
```

Использование `index` как `key` нежелательно при изменяемом списке. Здесь список статичный, но лучше использовать стабильный идентификатор (например, `feature.title`).

---

## 15. `Stats` — захардкоженные данные

```7:11:frontend/components/marketing/Stats.tsx
  const stats = [
    { value: '47', label: 'sessions' },
    { value: '143', label: 'messages' },
    { value: '12,450', label: 'tokens' },
  ];
```

Значения статичны. Для продакшена логичнее подгружать реальную статистику.

---

## 16. `DemoBlock` — неверный `ref`

В `DemoBlock` второй `motion.div` использует `isInView` из первого `useScrollAnimation`, но у него нет своего `ref`. Анимация второго блока привязана к видимости первого, что может быть нежелательно.

---

## 17. Landing page — несоответствие стилей загрузки

```29:33:frontend/app/(marketing)/page.tsx
  if (!checked) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="animate-pulse text-slate-600">Loading...</div>
```

Состояние загрузки использует `bg-slate-50`, тогда как основная страница — `bg-[#0A0A0F]`. При переключении возможен визуальный скачок.

---

## 18. Embed URL в dashboard

```104:104:frontend/app/(app)/dashboard/page.tsx
    return `<div id="ai-chat-widget" data-api-key="${apiKey ?? ""}"></div>\n<script src="${API_URL}/embed.js"></script>`;
```

`API_URL` — это `NEXT_PUBLIC_API_URL`. Для embed-скрипта нужен URL бэкенда. Если фронт и бэк на разных доменах, `API_URL` должен указывать на бэкенд.

---

## Резюме по приоритетам

| Приоритет | Проблема |
|-----------|----------|
| Высокий   | `/review` не защищён middleware |
| Высокий   | CORS `allow_credentials=False` при использовании cookies |
| Средний   | Устаревший `datetime.utcnow()` |
| Средний   | CTA-кнопки без навигации |
| Средний   | Двойной вызов `load()` при загрузке документов |
| Низкий    | Мёртвый код (Button, Card, use-mobile) |
| Низкий    | Хардкод GitHub, статичные Stats |
| Низкий    | Мелкие улучшения типов и обработки ошибок |
