# MR AI RAG v2 🧠

> **Production-ready RAG backend + Web UI**  
> Multi-provider AI · Local embeddings · Anti-hallucination · Zero SaaS dependency

---

## 📁 Project Structure

```
mr_ai_rag/
│
├── app/
│   ├── main.py                      # FastAPI app + serves frontend
│   ├── core/
│   │   └── config.py                # Pydantic settings (env vars)
│   ├── models/
│   │   └── schemas.py               # All Pydantic models
│   ├── routes/
│   │   ├── health.py                # GET  /api/health
│   │   ├── provider.py              # GET  /api/provider
│   │   │                            # POST /api/provider/config
│   │   ├── upload.py                # POST /api/upload
│   │   └── query.py                 # POST /api/ask
│   └── services/
│       ├── pdf_parser.py            # PyMuPDF text extraction
│       ├── chunker.py               # Sentence-aware chunking
│       ├── embedder.py              # Local sentence-transformers
│       ├── vector_store.py          # FAISS + metadata persistence
│       └── llm.py                   # OpenAI|Gemini|Claude|Ollama|HuggingFace
│
├── frontend/
│   └── index.html                   # Full SPA UI (no build step!)
│
├── vector_store/                    # Auto-created FAISS index
├── uploads/                         # Uploaded PDFs
├── requirements.txt
├── .env.example
└── README.md
```

---

## ⚙️ Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** — the UI loads automatically.

---

## 🤖 Supported AI Providers

| Provider | Key Required | Key Format | Notes |
|---|---|---|---|
| **OpenAI** | ✅ | `sk-...` | GPT-4o, GPT-4o-mini, etc. |
| **Google Gemini** | ✅ | `AIza...` | Gemini 1.5 Flash/Pro |
| **Anthropic Claude** | ✅ | `sk-ant-...` | Claude 3.5 Haiku/Sonnet |
| **Ollama** | ❌ Free | — | Runs locally, needs Ollama installed |
| **HuggingFace** | ✅ | `hf_...` | Inference API, many models |

**Switch providers at runtime** in the UI — no server restart needed.

---

## 📡 API Reference

### `GET /api/health`
```bash
curl http://localhost:8000/api/health
```

### `GET /api/provider`
```bash
curl http://localhost:8000/api/provider
```

### `POST /api/provider/config` — Set provider at runtime
```bash
# OpenAI
curl -X POST http://localhost:8000/api/provider/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-...","model":"gpt-4o-mini"}'

# Gemini
curl -X POST http://localhost:8000/api/provider/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"gemini","api_key":"AIza...","model":"gemini-2.5-flash"}'

# Claude
curl -X POST http://localhost:8000/api/provider/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"claude","api_key":"sk-ant-...","model":"claude-3-5-haiku-20241022"}'

# Ollama (no key needed)
curl -X POST http://localhost:8000/api/provider/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"llama3","ollama_url":"http://localhost:11434"}'

# HuggingFace
curl -X POST http://localhost:8000/api/provider/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"huggingface","api_key":"hf_...","model":"mistralai/Mistral-7B-Instruct-v0.2"}'
```

### `POST /api/upload`
```bash
curl -X POST http://localhost:8000/api/upload -F "file=@document.pdf"
```

### `POST /api/ask`
```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the key findings?","top_k":5}'
```

---

## 🎨 Frontend UI Features

- **Provider selector** — click any of the 5 providers
- **API key input** — masked field, auto-hidden for Ollama
- **Model dropdown** — all available models per provider
- **Drag & drop PDF upload**
- **Live indexed document list**
- **Chat interface** with typing animation
- **Cited sources** with filename, page number, similarity %
- **Toast notifications** for all actions
- **Status indicator** — live connection check

---

## 🛡️ Anti-Hallucination

When no relevant document context is found (cosine similarity < 0.25), the LLM is **never called**. The system returns:

> *"The requested information is not available in the provided documents."*

---

## 🚀 Ollama Setup (Free Local LLM)

```bash
# Install: https://ollama.com
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3

# Then in the UI: select Ollama, enter model name, click Apply
```
