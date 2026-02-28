# MR AI RAG v2 — API Key System & Docs Page

## Phase 1: Planning
- [x] Analyze all project files (routes, models, services, config)
- [/] Write implementation plan
- [ ] Get user approval

## Phase 2: Backend — API Key Management
- [ ] Create `app/core/api_keys.py` — key store, generate, validate
- [ ] Create `app/routes/apikeys.py` — CRUD endpoints
- [ ] Add API key dependency (`X-API-Key` header) to all protected routes
- [ ] Register `apikeys` router in [app/main.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/main.py)
- [ ] Add `API_KEY_ADMIN_SECRET` to [.env](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/.env) and [config.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/config.py)

## Phase 3: Frontend — API Docs Page
- [ ] Create `frontend/api-docs.html` — full standalone API documentation page
  - [ ] Section: Authentication (how to get & use API key)
  - [ ] Section: PDF Upload
  - [ ] Section: Video Upload
  - [ ] Section: YouTube Video
  - [ ] Section: JSON Upload
  - [ ] Section: Website URL
  - [ ] Section: Ask Question
  - [ ] Section: Provider Config
  - [ ] Section: Health Check
  - [ ] Code examples for each endpoint (curl + fetch)
  - [ ] Interactive "Try It" buttons (live fetch from docs page)

## Phase 4: Frontend — API Key Management UI
- [ ] Add API Keys management tab/modal to existing [frontend/index.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/index.html)
  - [ ] Generate key button
  - [ ] List existing keys (masked)
  - [ ] Revoke key button
  - [ ] Copy key to clipboard

## Phase 5: Verification
- [ ] Start server and test all API key endpoints manually
- [ ] Test that protected routes reject requests without valid key
- [ ] Test that docs page loads and all code examples work
- [ ] Test key generation, listing, and revocation flow
