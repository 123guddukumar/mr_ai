# MR AI RAG v2 — Fix & Enhancement Walkthrough

## What Was Fixed

### 🔴 Bug 1: Config Crash (`GEMINI_API_KEY2` in .env)
**File:** [app/core/config.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/config.py)  
The [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env) had `GEMINI_API_KEY2` not declared in the Settings class. Pydantic v2 defaults to `extra="forbid"`, crashing the server on startup.

```diff
  class Config:
      env_file = ".env"
      env_file_encoding = "utf-8"
+     extra = "ignore"   # ← allow unknown .env vars
```

### 🔴 Bug 2: `API_KEYS_ENABLED` Not Being Read
**File:** [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env)  
Pydantic booleans require lowercase `true`/`false`. The file had `True` (Python style).

```diff
- API_KEYS_ENABLED=True
+ API_KEYS_ENABLED=true
```

### 🔴 Bug 3: API Key Guard Never Applied 
**Files:** [upload.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/upload.py), [query.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/query.py), [website.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/website.py), [youtube.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/youtube.py), [jsondata.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/jsondata.py)  
Even though `API_KEYS_ENABLED=true`, **none** of the routes had [require_api_key](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/api_keys.py#158-176) dependency. Fixed all 10 endpoints:

```python
# Before (unprotected)
async def upload_document(file: UploadFile = File(...)):

# After (protected)
async def upload_document(file: UploadFile = File(...), _key: dict = Depends(require_api_key)):
```

Routes protected: `/upload`, `/upload-batch`, `/suggest-prompts`, `/ask`, `/ingest-youtube`, `/ingest-video`, `/video-summary`, `/video-quiz`, `/ingest-url`, `/ingest-json-url`, `/ingest-json-file`, `/preview-json-url`, `/json-records`

### 🔴 Bug 4: Route Ordering Conflict in apikeys.py
`GET /keys/status` and `POST /keys/validate` were declared **after** `DELETE /keys/{key_id}`. FastAPI would route `/keys/status` as a delete key with `key_id="status"`.

Fixed by moving static routes **before** the wildcard `/{key_id}` route.

---

## New: Premium API Docs Page (`/api-docs`)

**File:** [frontend/api-docs.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/api-docs.html)

The complete API documentation page includes:

| Section | Features |
|---------|----------|
| **Overview** | Live status bar, server health, LLM provider info |
| **Authentication** | List of protected vs public routes |
| **Generate Key** | Form to create keys with admin secret |
| **Manage Keys** | Table of all keys with revoke button |
| **Validate Key** | Check if any key is valid |
| **PDF Upload** | Docs + Try-It panel |
| **Ask Question** | Docs + Try-It panel |
| **YouTube** | Docs + Try-It panel |
| **Video Upload** | Docs + code examples |
| **Website URL** | Docs + Try-It panel |
| **JSON Data** | All 4 endpoints documented |
| **Health** | Live try-it button |
| **LLM Provider** | Runtime switching docs |

---

## How to Restart the Server

After these changes, restart the server to pick up the [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env) update:

```powershell
# Stop existing server (Ctrl+C), then:
cd c:\Users\LENOVO\Downloads\mr_ai_rag_v2\mr_ai_rag_v2
.\venv\Scripts\uvicorn.exe app.main:app --port 8000 --reload
```

Then open: **http://localhost:8000/api-docs**

---

## How to Use API Keys

1. Go to **http://localhost:8000/api-docs** → "Generate Key" section
2. Enter any name, your admin secret (default: `change-me-admin-secret`)
3. Copy the key (shown **only once**)
4. Add to every request as header: `X-API-Key: mrairag-...`

### Change the Admin Secret (Important!)
Edit [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env):
```
API_KEY_ADMIN_SECRET=your-strong-secret-here
```

---

## Verification Results

| Test | Result |
|------|--------|
| Server starts without errors | ✅ |
| `/api/health` returns OK | ✅ (770 chunks indexed) |
| `/api/keys/status` works | ✅ |
| Config loads with extra .env vars | ✅ (extra='ignore') |
| API Docs page renders | ✅ (dark theme, all sections, live key management) |
| `API_KEYS_ENABLED` = True in Python | ✅ (after .env fix) |
