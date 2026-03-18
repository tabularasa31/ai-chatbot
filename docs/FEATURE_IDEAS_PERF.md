# Performance & Scaling Ideas

### [FI-013] Chat sessions performance (N+1 and pagination)

**Идея:** Оптимизировать производительность логов чатов при большом количестве сессий.

**Почему:**
- `list_chat_sessions()` сейчас делает N+1 запрос:
  - один запрос по Chat,
  - по одному запросу по Message на каждую сессию.
- При сотнях/тысячах сессий это может стать узким местом.

**Что сделать:**
- Убрать N+1 в list_chat_sessions:
  - либо использовать join + group_by и агрегаты (COUNT, MAX, LAST_VALUE),
  - либо joinedload(Chat.messages) с пост-обработкой в Python.
- Добавить пагинацию/лимит:
  - `GET /chat/sessions?limit=50&offset=0`
  - хранить общее количество/has_more.

**Приоритет:** P3 — не критично на раннем этапе, но важно для масштабирования.

---

(см. основной список в FEATURE_IDEAS.md)
