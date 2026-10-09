from .base import LLMProvider
from .openrouter import OpenRouterProvider
from .nvidia import NvidiaProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from .google import GoogleProvider
from .groq import GroqProvider
from .together import TogetherProvider
from .cerebras import CerebrasProvider
from .mistral import MistralProvider
from .fireworks import FireworksProvider
from .replicate import ReplicateProvider
from .cohere import CohereProvider

_PROVIDERS: dict[str, LLMProvider] = {
    p.provider_id: p
    for p in (
        OpenRouterProvider(),
        NvidiaProvider(),
        OpenAIProvider(),
        AnthropicProvider(),
        GoogleProvider(),
        GroqProvider(),
        TogetherProvider(),
        CerebrasProvider(),
        MistralProvider(),
        FireworksProvider(),
        ReplicateProvider(),
        CohereProvider(),
    )
}

def get_provider(provider_id: str) -> LLMProvider:
    if provider_id not in _PROVIDERS:
        raise ValueError(f'Unknown provider: {provider_id}')
    return _PROVIDERS[provider_id]

def list_providers() -> list[str]:
    return list(_PROVIDERS)
