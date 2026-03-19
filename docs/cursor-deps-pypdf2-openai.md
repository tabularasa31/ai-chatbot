# Deps: Remove PyPDF2, update openai

## Проблемы

1. **`PyPDF2==3.0.1`** — deprecated. Авторы переименовали пакет в `pypdf`. Оба стоят одновременно — дублирование. Используется только `PyPDF2`, `pypdf` висит мёртвым грузом.
2. **`openai==1.6.0`** — декабрь 2023, сейчас актуальна `1.70+`. Обновить до последней стабильной.

---

## Что нужно сделать

### 1. Заменить PyPDF2 → pypdf в `requirements.txt`

```
# Убрать:
PyPDF2==3.0.1

# pypdf уже есть, оставить (обновить до актуальной):
pypdf==3.17.1  →  pypdf>=4.0.0
```

### 2. Обновить импорт в `backend/documents/parsers.py`

```python
# Было:
from PyPDF2 import PdfReader

# Стало:
from pypdf import PdfReader
```

API совместимо — `PdfReader` в `pypdf` работает так же, код парсера менять не нужно. Проверить что тесты проходят.

### 3. Обновить `openai` в `requirements.txt`

```
# Было:
openai==1.6.0

# Стало:
openai>=1.70.0
```

Проверить совместимость с текущим кодом в:
- `backend/core/openai_client.py`
- `backend/embeddings/service.py`
- `backend/search/service.py`
- `backend/chat/service.py`

API `openai` 1.x стабильный, breaking changes между 1.6 и 1.70 минимальны. Основные методы (`embeddings.create`, `chat.completions.create`) не менялись.

---

## Файлы для изменения

1. **`backend/requirements.txt`** — убрать `PyPDF2`, обновить `pypdf` и `openai`
2. **`backend/documents/parsers.py`** — заменить импорт `PyPDF2` → `pypdf`

---

## Текущий `requirements.txt` (relevant lines)

```
openai==1.6.0
pypdf==3.17.1
PyPDF2==3.0.1
```

## Текущий `parsers.py` (relevant)

```python
from PyPDF2 import PdfReader

def parse_pdf(content: bytes) -> str:
    """Extract text from PDF using PyPDF2."""
    reader = PdfReader(BytesIO(content))
    ...
```
