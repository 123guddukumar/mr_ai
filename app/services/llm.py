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
        "groq": settings.GROQ_MODEL,
        "huggingface": settings.HF_MODEL_ID,
    }.get(p, "unknown")


def get_active_api_key(provider: str) -> str:
    if _runtime["api_key"]:
        return _runtime["api_key"]
    return {
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "claude": settings.CLAUDE_API_KEY,
        "groq": settings.GROQ_API_KEY,
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
        "groq": _call_groq,
        "huggingface": _call_huggingface,
    }
    fn = dispatch.get(provider)
    if not fn:
        raise ValueError(f"Unsupported provider: {provider}")
    return await fn(question, context)


async def _call_groq(question: str, context: str) -> str:
    """Groq API (OpenAI compatible) with automatic fallbacks for rate limits."""
    api_key = get_active_api_key("groq")
    model = get_active_model()
    if not api_key: return "Groq API Key missing."
    
    prompt = build_prompt(question, context)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=settings.OPENAI_MAX_TOKENS,
            temperature=settings.OPENAI_TEMPERATURE
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq error: {e}. Checking for rate limits and trying fallbacks...")
        err_msg = str(e).lower()
        if "rate limit" in err_msg or "429" in err_msg:
            # Try alternative Groq models first
            alt_groq_models = ["llama-3.1-8b-instant"]
            for alt_model in alt_groq_models:
                if alt_model != model:
                    try:
                        logger.info(f"RAG Groq fallback: trying model {alt_model}")
                        resp = await client.chat.completions.create(
                            model=alt_model,
                            messages=[
                                {"role": "system", "content": settings.SYSTEM_PROMPT},
                                {"role": "user", "content": prompt}
                            ],
                            max_tokens=settings.OPENAI_MAX_TOKENS,
                            temperature=settings.OPENAI_TEMPERATURE
                        )
                        return resp.choices[0].message.content
                    except Exception as alt_err:
                        logger.warning(f"RAG Groq fallback {alt_model} failed: {alt_err}")
            
            # Try Google Gemini fallback
            gemini_key = settings.GEMINI_API_KEY
            if gemini_key:
                try:
                    gemini_model = settings.GEMINI_MODEL or "gemini-3.5-flash"
                    logger.info(f"RAG Gemini fallback: trying model {gemini_model}")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
                    payload = {
                        "system_instruction": {"parts": [{"text": settings.SYSTEM_PROMPT}]},
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
                    }
                    async with httpx.AsyncClient(timeout=60.0) as cl:
                        response = await cl.post(url, json=payload)
                        response.raise_for_status()
                        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                except Exception as gemini_err:
                    logger.warning(f"RAG Gemini fallback failed: {gemini_err}")
            
            # Try OpenAI fallback
            openai_key = settings.OPENAI_API_KEY
            if openai_key:
                try:
                    openai_model = settings.OPENAI_MODEL or "gpt-4o-mini"
                    logger.info(f"RAG OpenAI fallback: trying model {openai_model}")
                    from openai import AsyncOpenAI as AsyncOpenAIClient
                    openai_client = AsyncOpenAIClient(api_key=openai_key)
                    resp = await openai_client.chat.completions.create(
                        model=openai_model,
                        messages=[
                            {"role": "system", "content": settings.SYSTEM_PROMPT},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=settings.OPENAI_MAX_TOKENS,
                        temperature=settings.OPENAI_TEMPERATURE,
                    )
                    return resp.choices[0].message.content.strip()
                except Exception as openai_err:
                    logger.warning(f"RAG OpenAI fallback failed: {openai_err}")
                    
        return f"Error connecting to Groq (429 Rate Limit): {str(e)}"


async def generate_answer_with_config(
    question: str, context: str,
    provider: str, model: str, api_key: str,
    ollama_url: str = "http://localhost:11434",
) -> str:
    """
    Generate an answer using an explicit provider config (for personal memory bots).
    Does NOT read from _runtime state.
    """
    prompt = build_prompt(question, context)
    logger.info(f"Memory bot generating with {provider}/{model}")

    if provider == "gemini":
        if not api_key:
            raise RuntimeError("Gemini API key required for memory bot")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": settings.SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
        }
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(url, json=payload); r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    elif provider == "openai":
        if not api_key:
            raise RuntimeError("OpenAI API key required for memory bot")
        from openai import AsyncOpenAI
        cl = AsyncOpenAI(api_key=api_key)
        resp = await cl.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=settings.OPENAI_MAX_TOKENS,
            temperature=settings.OPENAI_TEMPERATURE,
        )
        return resp.choices[0].message.content.strip()

    elif provider == "claude":
        if not api_key:
            raise RuntimeError("Anthropic API key required for memory bot")
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        payload = {
            "model": model, "max_tokens": 1024,
            "system": settings.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip()

    elif provider == "ollama":
        payload = {
            "model": model, "stream": False,
            "messages": [
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=120.0) as hc:
            r = await hc.post(f"{ollama_url}/api/chat", json=payload); r.raise_for_status()
            return r.json()["message"]["content"].strip()

    elif provider == "huggingface":
        if not api_key:
            raise RuntimeError("HuggingFace API token required for memory bot")
        pmt = f"<s>[INST] {settings.SYSTEM_PROMPT}\n\n{prompt} [/INST]"
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(
                f"https://api-inference.huggingface.co/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"inputs": pmt, "parameters": {"max_new_tokens": 512, "temperature": 0.1, "return_full_text": False}},
            ); r.raise_for_status()
            return r.json()[0]["generated_text"].strip()

    elif provider == "groq":
        if not api_key:
            raise RuntimeError("Groq API key required for memory bot")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=settings.OPENAI_MAX_TOKENS,
            temperature=settings.OPENAI_TEMPERATURE
        )
        return resp.choices[0].message.content.strip()

    else:
        raise ValueError(f"Unsupported provider: {provider}")


async def generate_simple_response(prompt: str, system_prompt: str = "You are a helpful assistant.", max_tokens: int = 4096) -> str:
    """Generates a response without RAG boilerplate, with automatic rate limit fallbacks."""
    provider = get_active_provider()
    model = get_active_model()
    api_key = get_active_api_key(provider)
    
    try:
        if provider == "groq":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.7
            )
            return resp.choices[0].message.content.strip()
        
        elif provider == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key)
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.7
            )
            return resp.choices[0].message.content.strip()
            
        elif provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens}
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                
        elif provider == "claude":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["content"][0]["text"].strip()
                
        elif provider == "ollama":
            base_url = _runtime.get("ollama_url") or settings.OLLAMA_BASE_URL
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.7}
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(f"{base_url}/api/chat", json=payload)
                response.raise_for_status()
                return response.json()["message"]["content"].strip()
                
        elif provider == "huggingface":
            full_prompt = f"<s>[INST] {system_prompt}\n\n{prompt} [/INST]"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"inputs": full_prompt, "parameters": {"max_new_tokens": min(max_tokens, 1024), "temperature": 0.7, "return_full_text": False}}
            url = f"https://api-inference.huggingface.co/models/{model}"
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()[0]["generated_text"].strip()

        # Fallback to general generate_answer but with no context
        return await generate_answer(prompt, "")

    except Exception as primary_exc:
        # Rate limit or API error fallback layer
        err_msg = str(primary_exc).lower()
        logger.warning(f"Primary LLM generation failed ({provider}/{model}): {primary_exc}. Running fallbacks...")
        
        # --- Fallback 1: Try alternative Groq models
        if provider == "groq" or "groq" in err_msg:
            alt_groq_models = ["llama-3.1-8b-instant"]
            for alt_model in alt_groq_models:
                if alt_model != model:
                    try:
                        logger.info(f"Fallback: trying Groq model {alt_model}")
                        from openai import AsyncOpenAI
                        client = AsyncOpenAI(api_key=get_active_api_key("groq"), base_url="https://api.groq.com/openai/v1")
                        resp = await client.chat.completions.create(
                            model=alt_model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt}
                            ],
                            max_tokens=max_tokens,
                            temperature=0.7
                        )
                        return resp.choices[0].message.content.strip()
                    except Exception as alt_err:
                        logger.warning(f"Fallback Groq model {alt_model} failed: {alt_err}")
                        
        # --- Fallback 2: Try Google Gemini
        gemini_key = settings.GEMINI_API_KEY
        if gemini_key:
            try:
                gemini_model = settings.GEMINI_MODEL or "gemini-3.5-flash"
                logger.info(f"Fallback: trying Gemini model {gemini_model}")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
                payload = {
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens}
                }
                async with httpx.AsyncClient(timeout=60.0) as cl:
                    response = await cl.post(url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as gemini_err:
                logger.warning(f"Fallback Gemini failed: {gemini_err}")
                
        # --- Fallback 3: Try OpenAI
        openai_key = settings.OPENAI_API_KEY
        if openai_key:
            try:
                openai_model = settings.OPENAI_MODEL or "gpt-4o-mini"
                logger.info(f"Fallback: trying OpenAI model {openai_model}")
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=openai_key)
                resp = await client.chat.completions.create(
                    model=openai_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=0.7
                )
                return resp.choices[0].message.content.strip()
            except Exception as openai_err:
                logger.warning(f"Fallback OpenAI failed: {openai_err}")
                
        # Raise the original error if all fallbacks failed
        raise primary_exc

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

async def llm_with_history(
    question: str, system: str, history: list,
    provider: str, model: str, api_key: str, ollama_url: str = "",
) -> str:
    """Helper for chatting with history, used by Memory and Agent routes."""
    import httpx
    from app.core.config import settings

    if provider == "gemini":
        if not api_key:
            raise RuntimeError("Gemini API key required")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        contents = []
        for t in history:
            contents.append({"role": "user" if t.get("role") == "user" else "model",
                             "parts": [{"text": t.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": question}]})
        payload = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                   "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}}
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(url, json=payload); r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    elif provider == "openai":
        if not api_key:
            raise RuntimeError("OpenAI API key required")
        from openai import AsyncOpenAI
        msgs = [{"role": "system", "content": system}]
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        cl = AsyncOpenAI(api_key=api_key)
        resp = await cl.chat.completions.create(model=model, messages=msgs, max_tokens=1024, temperature=0.1)
        return resp.choices[0].message.content.strip()

    elif provider == "claude":
        if not api_key:
            raise RuntimeError("Anthropic API key required")
        hdrs = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        msgs = []
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        payload = {"model": model, "max_tokens": 1024, "system": system, "messages": msgs}
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post("https://api.anthropic.com/v1/messages", headers=hdrs, json=payload)
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip()

    elif provider == "ollama":
        msgs = [{"role": "system", "content": system}]
        for t in history:
            msgs.append({"role": t.get("role", "user"), "content": t.get("content", "")})
        msgs.append({"role": "user", "content": question})
        async with httpx.AsyncClient(timeout=120.0) as hc:
            r = await hc.post(f"{ollama_url}/api/chat", json={"model": model, "stream": False, "messages": msgs})
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    elif provider == "huggingface":
        if not api_key:
            raise RuntimeError("HuggingFace API key required")
        full_prompt = f"<s>[INST] {system}\n\nUser: {question} [/INST]"
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(
                f"https://api-inference.huggingface.co/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"inputs": full_prompt, "parameters": {"max_new_tokens": 512, "temperature": 0.1, "return_full_text": False}},
            ); r.raise_for_status()
            return r.json()[0]["generated_text"].strip()
    else:
        raise ValueError(f"Unsupported provider: {provider}")


async def translate_hinglish_prompt_to_english(prompt_text: str) -> str:
    """
    Checks if a prompt contains Hindi/Hinglish characters or words,
    and translates it to English using the active LLM.
    """
    if not prompt_text:
        return ""
    
    # Quick check: does it have non-ascii characters or common Hinglish words?
    hinglish_words = {"ek", "ladka", "baitha", "hai", "rakha", "room", "mein", "sirf", "ko", "se", "aur", "pe", "jo", "ki", "ka", "ladki", "akela", "baithi", "chhat", "khada", "khadi", "dekh", "rha", "rhi"}
    import re
    clean_words = set(re.findall(r'[a-zA-Z]+', prompt_text.lower()))
    
    has_hinglish = not clean_words.isdisjoint(hinglish_words) or any(ord(c) > 127 for c in prompt_text)
    
    if not has_hinglish:
        return prompt_text
        
    try:
        safe_prompt = prompt_text.encode('ascii', errors='replace').decode('ascii')
        logger.info(f"Translating Hinglish visual prompt: '{safe_prompt}'")
        system_prompt = (
            "You are a professional image generation prompt translator. "
            "Translate the given Hinglish/Hindi text describing a scene into a detailed, "
            "clear English description suitable for AI image generators (like Stable Diffusion / Flux). "
            "Do NOT include any introduction or conversation. Output ONLY the English translation."
        )
        translated = await generate_simple_response(prompt_text, system_prompt, max_tokens=1024)
        translated_clean = translated.strip().strip('"').strip("'")
        if translated_clean:
            safe_trans = translated_clean.encode('ascii', errors='replace').decode('ascii')
            logger.info(f"Translated success: '{safe_trans}'")
            return translated_clean
    except Exception as e:
        logger.warning(f"Failed to translate Hinglish prompt: {e}")
        
    return prompt_text

