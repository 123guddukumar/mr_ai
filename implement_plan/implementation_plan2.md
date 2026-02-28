# API Key System & API Docs Page — Implementation Plan

## Background

The MR AI RAG v2 project is a FastAPI RAG backend with these existing routes:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload single PDF |
| POST | `/api/upload-batch` | Upload up to 20 PDFs |
| POST | `/api/suggest-prompts` | AI-generated prompt suggestions |
| POST | `/api/ingest-url` | Ingest a website URL |
| POST | `/api/ingest-youtube` | Ingest YouTube video |
| POST | `/api/ingest-video` | Upload video/audio file |
| POST | `/api/video-summary` | LLM video summary |
| POST | `/api/video-quiz` | LLM quiz from video |
| POST | `/api/ingest-json-url` | Fetch JSON API & index |
| POST | `/api/ingest-json-file` | Upload .json file & index |
| POST | `/api/preview-json-url` | Preview JSON without indexing |
| GET | `/api/json-records` | Get raw JSON records |
| POST | `/api/ask` | Ask question from indexed docs |
| GET/POST | `/api/provider` | LLM provider status & config |
| GET | `/api/health` | System health check |

## Proposed Changes

---

### API Key Backend

#### [NEW] [api_keys.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/api_keys.py)

A pure-Python in-memory (JSON-file-persisted) API key store:
- `generate_api_key(name, created_by)` — creates a `mrairag-XXXX...` key, stores to `vector_store/api_keys.json`
- `validate_api_key(key)` — checks if key exists and is active
- `list_api_keys()` — returns all keys (masked except last 4 chars)
- `revoke_api_key(key_id)` — marks key as inactive
- `get_api_key_dependency()` — FastAPI `Depends()` for protected routes
- Keys stored as JSON with: [id](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/youtube.py#553-600), `key` (hashed), `name`, `created_at`, `is_active`, `last_used_at`, `request_count`

Keys stored at: `vector_store/api_keys.json`

#### [NEW] [apikeys.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/apikeys.py)

REST endpoints for API key management:
```
POST   /api/keys/generate   → Create new API key (requires admin secret)
GET    /api/keys             → List all keys (masked)
DELETE /api/keys/{key_id}    → Revoke a key (requires admin secret)
POST   /api/keys/validate    → Check if a key is valid (returns status)
```

Admin operations require `X-Admin-Secret` header matching `API_KEY_ADMIN_SECRET` in [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env).

---

#### [MODIFY] [config.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/config.py)

Add:
```python
API_KEY_ADMIN_SECRET: str = "change-me-admin-secret"
API_KEYS_ENABLED: bool = True
```

---

#### [MODIFY] [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env)

Add:
```env
API_KEY_ADMIN_SECRET=your-secret-admin-password
API_KEYS_ENABLED=true
```

---

#### [MODIFY] [main.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/main.py)

- Import and register `apikeys` router with tag `["API Keys"]`

---

### Frontend — API Docs Page

#### [NEW] [api-docs.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/api-docs.html)

A beautiful, standalone single-page API docs page (no framework, pure HTML/CSS/JS):

**Design:**
- Dark mode, sidebar navigation, premium glassmorphism cards
- Same brand colors as the existing frontend (purple/indigo gradient)
- Smooth scrollspy sidebar highlighting

**Sections:**
1. **Getting Started** — what MR AI RAG is, base URL
2. **Authentication** — how to get an API key, how to include it (`X-API-Key` header)
3. **PDF Upload** — `/api/upload` and `/api/upload-batch`
4. **Video Upload** — `/api/ingest-video` (with Whisper)
5. **YouTube Video** — `/api/ingest-youtube`
6. **JSON Upload** — `/api/ingest-json-file` and `/api/ingest-json-url`
7. **Website URL** — `/api/ingest-url`
8. **Ask Question** — `/api/ask`
9. **Provider Config** — `/api/provider` and `/api/provider/config`
10. **Health Check** — `/api/health`
11. **API Key Management** — generate, list, revoke

**Per-endpoint:**
- HTTP method badge (POST/GET), endpoint path
- Description
- Request parameters table (body fields, query params, headers)
- Response schema table
- `curl` code example (syntax highlighted)
- JavaScript [fetch](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/jsondata.py#234-239) code example
- ✅ **"Try It"** live button — opens an interactive form to send request to running server

---

#### [MODIFY] [index.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/index.html)

- Add **"API Keys"** tab or settings section in the frontend
- Shows user's active keys (masked), lets them generate and copy new keys
- Add a prominent **"API Docs →"** link in the header/navigation

## User Review Required

> [!IMPORTANT]
> **API Key Protection is Optional by Default**  
> By default, `API_KEYS_ENABLED=false` — all existing routes continue to work without a key. When you set `API_KEYS_ENABLED=true`, all data-ingestion and query routes will require `X-API-Key` header. Health and provider status remain public.

> [!NOTE]
> **Keys are stored locally in JSON file**  
> Keys are persisted to `vector_store/api_keys.json`. If you restart the server, keys remain valid. The actual key value is only shown once at creation time — it is stored as a SHA-256 hash afterwards.

> [!NOTE]
> **Admin Secret for Key Management**  
> You need to set `API_KEY_ADMIN_SECRET` in [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env) to protect key generation and revocation. Default value is `change-me-admin-secret` — **please change this**.

## Verification Plan

### Manual Testing (Browser)
1. Run the server: `cd c:\Users\LENOVO\Downloads\mr_ai_rag_v2\mr_ai_rag_v2 && python -m uvicorn app.main:app --reload --port 8000`
2. Open `http://localhost:8000` — verify "API Keys" section in UI and "API Docs" link in header
3. Open `http://localhost:8000/api-docs` — verify the API docs page loads with all sections
4. Test key generation via the UI — copy the generated key
5. Test `/api/ask` with and without key when `API_KEYS_ENABLED=true`

### API Endpoint Tests (curl)
```bash
# Generate a key
curl -X POST http://localhost:8000/api/keys/generate \
  -H "X-Admin-Secret: change-me-admin-secret" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-key"}'

# List keys
curl http://localhost:8000/api/keys \
  -H "X-Admin-Secret: change-me-admin-secret"

# Validate a key
curl -X POST http://localhost:8000/api/keys/validate \
  -H "Content-Type: application/json" \
  -d '{"key": "mrairag-XXXX..."}'

# Use API key for upload
curl -X POST http://localhost:8000/api/upload \
  -H "X-API-Key: mrairag-XXXX..." \
  -F "file=@test.pdf"
```
