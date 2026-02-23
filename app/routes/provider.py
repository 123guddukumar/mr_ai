"""
MR AI RAG - Provider Configuration Route
GET  /provider        → get current provider status
POST /provider/config → set provider + API key at runtime
"""

import logging
from fastapi import APIRouter, HTTPException
from app.models.schemas import ProviderConfigRequest, ProviderConfigResponse, ProviderStatusResponse
from app.services.llm import set_runtime_provider, get_active_provider, get_active_model

logger = logging.getLogger(__name__)
router = APIRouter()

# Default models per provider
PROVIDER_DEFAULTS = {
    "openai":       {"model": "gpt-4o-mini",                          "requires_key": True,  "label": "OpenAI GPT"},
    "gemini":       {"model": "gemini-2.5-flash",                     "requires_key": True,  "label": "Google Gemini"},
    "claude":       {"model": "claude-3-5-haiku-20241022",            "requires_key": True,  "label": "Anthropic Claude"},
    "ollama":       {"model": "llama3",                               "requires_key": False, "label": "Ollama (Local)"},
    "huggingface":  {"model": "mistralai/Mistral-7B-Instruct-v0.2",   "requires_key": True,  "label": "HuggingFace"},
}

PROVIDER_MODELS = {
    "openai":      ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    "gemini":      ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-001"],
    "claude":      ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
    "ollama":      ["llama3", "llama3.1", "mistral", "phi3", "gemma2", "deepseek-r1"],
    "huggingface": ["mistralai/Mistral-7B-Instruct-v0.2", "HuggingFaceH4/zephyr-7b-beta", "tiiuae/falcon-7b-instruct"],
}


@router.get("/provider", response_model=ProviderStatusResponse, summary="Get current LLM provider status")
async def get_provider_status():
    providers_list = []
    for key, info in PROVIDER_DEFAULTS.items():
        providers_list.append({
            "id": key,
            "label": info["label"],
            "requires_key": info["requires_key"],
            "default_model": info["model"],
            "available_models": PROVIDER_MODELS.get(key, []),
        })
    return ProviderStatusResponse(
        current_provider=get_active_provider(),
        current_model=get_active_model(),
        providers=providers_list,
    )


@router.post("/provider/config", response_model=ProviderConfigResponse, summary="Set LLM provider and API key")
async def configure_provider(req: ProviderConfigRequest):
    if req.provider not in PROVIDER_DEFAULTS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    info = PROVIDER_DEFAULTS[req.provider]

    # Validate API key required providers
    if info["requires_key"] and not req.api_key:
        raise HTTPException(
            status_code=400,
            detail=f"{info['label']} requires an API key."
        )

    model = req.model or info["model"]

    set_runtime_provider(
        provider=req.provider,
        api_key=req.api_key or "",
        model=model,
        ollama_url=req.ollama_url or "http://localhost:11434"
    )

    return ProviderConfigResponse(
        success=True,
        provider=req.provider,
        model=model,
        message=f"Provider set to {info['label']} using model {model}."
    )
