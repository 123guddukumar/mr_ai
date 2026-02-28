# MR AI RAG — Client Auth & Dashboard

## Tasks

- [x] Read existing codebase (api_keys.py, config.py, main.py)
- [x] Write implementation plan and get approval

### Backend
- [x] Create [app/core/clients.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/clients.py) — client storage (JSON file), register/login/validate
- [x] Create [app/routes/clients.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/clients.py) — REST endpoints
- [x] Update [app/routes/apikeys.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/routes/apikeys.py) — accept client token OR global admin secret
- [x] Register client routes + serve new pages in [app/main.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/main.py)

### Frontend
- [x] Create [frontend/login.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/login.html) — register + login form → saves token, redirects to /dashboard
- [x] Create [frontend/dashboard.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/dashboard.html) — shows client ID, API key generator (uses client token as admin secret), chat history
- [x] Update [frontend/index.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/index.html) — add Login button in header
- [x] Update [frontend/api-docs.html](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/frontend/api-docs.html) — add Login link in sidebar

### Verification
- [x] Register a new client → get client_id
- [x] Login → redirect to dashboard
- [x] Generate API key from dashboard (client token = admin secret)
- [x] Verify API key works on `/api/ask`
