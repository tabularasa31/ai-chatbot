# Feature: [FI-009] Improved chunking + metadata

## Контекст

Текущий чанкинг в `backend/embeddings/service.py`:

```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap if overlap < chunk_size else end
    return chunks
```

**Проблемы:**
1. Режет по символам — чанк может начинаться/заканчиваться посередине слова или предложения
2. В `metadata_json` хранится только `{"chunk_index": i}` — нет информации о позиции в тексте
3. Нет учёта структуры документа (заголовки, параграфы, списки)

---

## Что нужно сделать

### 1. Улучшить `chunk_text` — резать по предложениям, не по символам

Новая логика:
- Разбить текст на предложения (по `. `, `\n`, `? `, `! `)
- Набирать чанк до `chunk_size` символов, не обрывая предложение на середине
- Overlap — добавлять последнее предложение предыдущего чанка в начало следующего

```python
def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap_sentences: int = 1,
) -> list[dict]:
    """
    Split text into chunks by sentences.
    
    Returns list of dicts:
    {
        "text": str,          # chunk content
        "chunk_index": int,   # position in document
        "char_offset": int,   # start char position in original text
        "char_end": int,      # end char position in original text
    }
    """
```

### 2. Обогатить `metadata_json` в модели Embedding

Сейчас: `{"chunk_index": i}`

Нужно: 
```python
{
    "chunk_index": 0,
    "char_offset": 0,       # начало чанка в исходном тексте
    "char_end": 487,        # конец чанка
    "filename": "FAQ.pdf",  # имя документа (для debug)
    "file_type": "pdf",     # тип документа
}
```

### 3. Обновить `create_embeddings_for_document`

Передавать дополнительные метаданные из документа при создании эмбеддингов:

```python
for i, item in enumerate(response.data):
    chunk_meta = chunks[i]  # теперь chunk — это dict, не str
    emb = Embedding(
        document_id=document_id,
        chunk_text=chunk_meta["text"],
        vector=item.embedding,
        metadata_json={
            "chunk_index": chunk_meta["chunk_index"],
            "char_offset": chunk_meta["char_offset"],
            "char_end": chunk_meta["char_end"],
            "filename": doc.filename,
            "file_type": doc.file_type.value,
        },
    )
```

---

## Файлы для изменения

1. **`backend/embeddings/service.py`** — основные изменения:
   - `chunk_text()` — новая логика по предложениям, возвращает `list[dict]`
   - `create_embeddings_for_document()` — использует новый формат чанков

2. **Тесты** — обновить `tests/test_embeddings.py` под новый формат `chunk_text`

---

## Требования к chunk_text

- Чанк не должен обрываться посередине слова
- Размер чанка — мягкое ограничение (может быть чуть больше `chunk_size` если предложение длинное)
- Если одно предложение > `chunk_size` — оставить как есть, не дробить
- Пустые чанки пропускать
- Работает для PDF, Markdown, Swagger (plain text после парсинга)

---

## Текущий код для reference

```python
# backend/embeddings/service.py — текущий chunk_text
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap if overlap < chunk_size else end
    return chunks

# create_embeddings_for_document — как используется chunk_text
chunks = chunk_text(doc.parsed_text)
# ...
for i, item in enumerate(response.data):
    emb = Embedding(
        document_id=document_id,
        chunk_text=chunks[i],
        vector=item.embedding,
        metadata_json={"chunk_index": i},
    )
```

---

## Модель Embedding (для справки)

```python
class Embedding(Base):
    chunk_text = Column(Text, nullable=False)
    vector = Column(Vector(1536), nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
```

Никаких изменений в модели или миграциях не нужно — `metadata_json` уже JSON, просто кладём туда больше данных.
