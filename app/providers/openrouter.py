from .base import LLMProvider

class OpenRouterProvider(LLMProvider):
    provider_id = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    validation_model = "meta-llama/llama-3.1-8b-instruct"   # verify it exists