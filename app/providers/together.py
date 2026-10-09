from .base import LLMProvider

class TogetherProvider(LLMProvider):
    provider_id = 'together'
    display_name = 'Together AI'
    base_url = 'https://api.together.xyz/v1'
    validation_model = 'meta-llama/Llama-3.1-8B-Instruct'
