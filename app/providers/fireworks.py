from .base import LLMProvider

class FireworksProvider(LLMProvider):
    provider_id = 'fireworks'
    display_name = 'Fireworks AI'
    base_url = 'https://api.fireworks.ai/inference/v1'
    validation_model = 'accounts/fireworks/models/llama-v3p1-8b-instruct'
