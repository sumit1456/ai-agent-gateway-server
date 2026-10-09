from .base import LLMProvider

class MistralProvider(LLMProvider):
    provider_id = 'mistral'
    display_name = 'Mistral AI'
    base_url = 'https://api.mistral.ai/v1'
    validation_model = 'mistral-small-latest'
