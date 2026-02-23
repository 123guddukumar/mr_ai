"""
MR AI RAG - LLM Service
Modular LLM: OpenAI | Gemini | Claude | Ollama | HuggingFace
Runtime provider switching without restart.
"""

import logging
import httpx
from typing import List, Tuple
from app.core.config import settings
from app.models.schemas import ChunkMetadata, SourceCitation

logger = logging.getLogger(__name__)

# ── Runtime State (overrides settings at runtime) ─────────────────────────────
_runtime = {
    "provider": None,      # None = use settings.LLM_PROVIDER
    "api_key": None,
    "model": None,
    "ollama_url": None,
}


def set_runtime_provider(provider: str, api_key: str = "", model: str = "", ollama_url: str = ""):
    """Update LLM provider at runtime without restart."""
    _runtime["provider"] = provider
    _runtime["api_key"] = api_key or ""
    _runtime["model"] = model or ""
    _runtime["ollama_url"] = ollama_url or settings.OLLAMA_BASE_URL
    logger.info(f"Runtime provider set to: {provider} / {model or '(default)'}")


def get_active_provider() -> str:
    return _runtime["provider"] or settings.LLM_PROVIDER


def get_active_model() -> str:
    if _runtime["model"]:
        return _runtime["model"]
    p = get_active_provider()
    return {
        "openai": settings.OPENAI_MODEL,
        "gemini": settings.GEMINI_MODEL,
        "claude": settings.CLAUDE_MODEL,
        "ollama": settings.OLLAMA_MODEL,
        "huggingface": settings.HF_MODEL_ID,
    }.get(p, "unknown")


def get_active_api_key(provider: str) -> str:
    if _runtime["api_key"]:
        return _runtime["api_key"]
    return {
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "claude": settings.CLAUDE_API_KEY,
        "ollama": "",
        "huggingface": settings.HF_API_KEY,
    }.get(provider, "")


# ── Context Builder ───────────────────────────────────────────────────────────

def build_context_and_sources(
    results: List[Tuple[ChunkMetadata, float]]
) -> Tuple[str, List[SourceCitation]]:
    context_parts = []
    sources = []
    for chunk, score in results:
        context_parts.append(
            f"[Source: {chunk.source_file}, Page {chunk.page_number}]\n{chunk.text}"
        )
        sources.append(SourceCitation(
            filename=chunk.source_file,
            page_number=chunk.page_number,
            excerpt=chunk.text[:300] + ("..." if len(chunk.text) > 300 else ""),
            similarity_score=round(score, 4)
        ))
    context = "\n\n---\n\n".join(context_parts)
    return context, sources


def build_prompt(question: str, context: str) -> str:
    return (
        f"Context from documents:\n\n{context}\n\n---\n\n"
        f"Question: {question}\n\n"
        "Answer strictly based on the context above. Cite the source file and page number."
    )


# ── Router ────────────────────────────────────────────────────────────────────

async def generate_answer(question: str, context: str) -> str:
    provider = get_active_provider()
    logger.info(f"Generating with provider: {provider}")
    dispatch = {
        "openai": _call_openai,
        "gemini": _call_gemini,
        "claude": _call_claude,
        "ollama": _call_ollama,
        "huggingface": _call_huggingface,
    }
    fn = dispatch.get(provider)
    if not fn:
        raise ValueError(f"Unsupported provider: {provider}")
    return await fn(question, context)


# ── OpenAI ────────────────────────────────────────────────────────────────────

async def _call_openai(question: str, context: str) -> str:
    api_key = get_active_api_key("openai")
    model = get_active_model()
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured.")
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": settings.SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(question, context)}
        ],
        max_tokens=settings.OPENAI_MAX_TOKENS,
        temperature=settings.OPENAI_TEMPERATURE,
    )
    return response.choices[0].message.content.strip()


# ── Google Gemini ─────────────────────────────────────────────────────────────

async def _call_gemini(question: str, context: str) -> str:
    api_key = get_active_api_key("gemini")
    model = get_active_model()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": settings.SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": build_prompt(question, context)}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Anthropic Claude ──────────────────────────────────────────────────────────

async def _call_claude(question: str, context: str) -> str:
    api_key = get_active_api_key("claude")
    model = get_active_model()
    if not api_key:
        raise RuntimeError("Claude API key is not configured.")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 1024,
        "system": settings.SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_prompt(question, context)}]
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"].strip()


# ── Ollama ────────────────────────────────────────────────────────────────────

async def _call_ollama(question: str, context: str) -> str:
    model = get_active_model()
    base_url = _runtime.get("ollama_url") or settings.OLLAMA_BASE_URL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": settings.SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(question, context)}
        ],
        "stream": False
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{base_url}/api/chat", json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()


# ── HuggingFace ───────────────────────────────────────────────────────────────

async def _call_huggingface(question: str, context: str) -> str:
    api_key = get_active_api_key("huggingface")
    model = get_active_model()
    if not api_key:
        raise RuntimeError("HuggingFace API key is not configured.")
    prompt = f"<s>[INST] {settings.SYSTEM_PROMPT}\n\n{build_prompt(question, context)} [/INST]"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 512, "temperature": 0.1, "return_full_text": False}}
    url = f"https://api-inference.huggingface.co/models/{model}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()[0]["generated_text"].strip()
