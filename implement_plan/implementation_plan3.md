# MR AI RAG v2 — Fix & Enhance

## Summary

Full code analysis revealed the following issues and improvements needed:

1. **Critical Bug**: `API_KEYS_ENABLED=True` in [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env), but **no route has the [require_api_key](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/api_keys.py#158-176) dependency**. Anyone can call upload/query/youtube/website/json endpoints without a key.
2. **Route Ordering Bug**: In [apikeys.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/apikeys.py), the `GET /keys/status` and `POST /keys/validate` static routes are declared **after** the wildcard `DELETE /keys/{key_id}` route — FastAPI will match `/keys/status` as a key_id delete, causing 404s. The order must be fixed.
3. **API Docs page**: Existing [api-docs.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/api-docs.html) needs a full rebuild with a proper API Key Management UI (generate/list/revoke) and interactive documentation for all endpoints.

---

## Proposed Changes

### Backend — API Key Enforcement

#### [MODIFY] [apikeys.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/apikeys.py)
- Move `GET /keys/status` and `POST /keys/validate` routes **before** `DELETE /keys/{key_id}` to fix route ordering conflict.
- No other logic changes needed.

#### [MODIFY] [upload.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/upload.py)
- Add `Depends(require_api_key)` to `POST /upload`, `POST /upload-batch`, `POST /suggest-prompts`.

#### [MODIFY] [query.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/query.py)
- Add `Depends(require_api_key)` to `POST /ask`.

#### [MODIFY] [youtube.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/youtube.py)
- Add `Depends(require_api_key)` to `/ingest-youtube`, `/ingest-video`, `/video-summary`, `/video-quiz`.

#### [MODIFY] [website.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/website.py)
- Add `Depends(require_api_key)` to `POST /ingest-url`.

#### [MODIFY] [jsondata.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/jsondata.py)
- Add `Depends(require_api_key)` to all 4 endpoints.

---

### Frontend — API Docs Page

#### [MODIFY] [api-docs.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/api-docs.html)
Complete rebuild of the API documentation page with:

1. **Header** — App name, version badge, live status indicator
2. **API Key Panel** (sticky sidebar/top):
   - Generate new key (name + created_by fields, calls `POST /api/keys/generate` with admin secret)
   - List all keys in a table (calls `GET /api/keys`)
   - Revoke key with one click (calls `DELETE /api/keys/{id}`)
   - Validate key section
3. **Endpoint Documentation sections**:
   - Authentication guide (how to pass `X-API-Key` header)
   - `POST /api/upload` — PDF upload with example
   - `POST /api/ask` — Question answering with example
   - `POST /api/ingest-youtube` — YouTube URL ingestion
   - `POST /api/ingest-video` — Video file upload
   - `POST /api/ingest-url` — Website scraping
   - `POST /api/ingest-json-file` — JSON file upload
   - `POST /api/ingest-json-url` — JSON URL ingestion
   - `GET /api/health` — Health check
   - `GET /api/provider` / `POST /api/provider/config` — LLM switching
4. **Code examples** (curl + Python fetch) per endpoint
5. **Try-It-Live** panels using the stored API key in the page

---

## Verification Plan

### Automated — Server Startup
```
cd c:\Users\LENOVO\Downloads\mr_ai_rag_v2\mr_ai_rag_v2
.\venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```
Server must start without errors.

### Manual Browser Tests (after server starts)
1. Open `http://localhost:8000/api-docs` → Docs page loads
2. Open `http://localhost:8000/api/docs` → Swagger UI loads
3. **Unauthenticated request** — In browser DevTools console run:
   ```js
   fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:'test'})}).then(r=>console.log(r.status))
   ```
   Expected: `401`
4. **Generate API key** via the docs page UI → Copy the key
5. **Authenticated request** — In browser DevTools console run:
   ```js
   fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'mrairag-...'}, body:JSON.stringify({question:'test'})}).then(r=>console.log(r.status))
   ```
   Expected: `200` or `404` (no docs indexed yet) — NOT `401`
6. **Revoke key** via docs page → repeat step 5 → expect `401`
