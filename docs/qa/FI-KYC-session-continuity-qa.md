# FI-KYC / session continuity QA

Проверка continuity и resume для widget/KYC после FI-KYC continuity v2.

## Scope

- `POST /widget/session/init`
- `POST /widget/chat`
- identified resume
- anonymous browser-local continuity
- closed-chat reset / `Start new chat`
- `user_sessions` lifecycle для identified users

## Preconditions

- есть активный tenant с настроенным OpenAI key
- для identified сценариев есть KYC signing secret и валидный `identity_token`
- для browser continuity удобно тестировать в обычном окне и в отдельном incognito окне

## Scenarios

| ID | Шаги | Ожидание |
|---|---|---|
| K1 | `POST /widget/session/init` без `identity_token` | `mode=anonymous`, виджет работает |
| K2 | `POST /widget/session/init` с валидным `identity_token` | `mode=identified`, возвращается `session_id` |
| K3 | Повторить `POST /widget/session/init` для того же `user_id` в течение 24h, если чат не закрыт | возвращается тот же `session_id` |
| K4 | Повторить `POST /widget/session/init` для того же `user_id` после закрытия чата | возвращается новый `session_id` |
| K5 | Повторить `POST /widget/session/init` для другого `user_id` | возвращается новая сессия |
| K6 | Anonymous user задаёт вопрос, закрывает браузер, открывает тот же сайт в том же браузере < 24h | чат продолжается в той же сессии |
| K7 | Anonymous user открывает тот же сайт в другом браузере / incognito | начинается новая сессия |
| K8 | После `chat_ended=true` widget показывает `Start new chat` | input больше не застревает в тупике; действие видно |
| K9 | Нажать `Start new chat`, затем отправить новый вопрос | создаётся новая сессия, старый закрытый чат не продолжается |
| K10 | В escalation follow-up ответить `yes` | чат остаётся открытым, `chat_ended=false` |
| K11 | В escalation follow-up ответить `no` | чат закрывается, `chat_ended=true` |

## DB / internal checks

Для identified-сценариев при технической проверке:

- в `chats.user_context` сохранён `user_id`
- для активного identified user есть одна активная строка в `user_sessions`
- `conversation_turns` растёт на каждый turn
- после закрытия чата у активной строки появляется `session_ended_at`

## Regression notes

- invalid or stale `session_id` в widget не должен ломать UX: виджет должен очистить его и начать новую сессию
- anonymous users не должны получать `user_sessions` rows
- закрытый чат не должен resume-иться даже для того же `user_id`
- другой tenant не должен получить доступ к чужой сессии по `session_id`
