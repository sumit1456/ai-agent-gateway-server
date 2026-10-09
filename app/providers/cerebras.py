from .base import LLMProvider

class CerebrasProvider(LLMProvider):
    provider_id = 'cerebras'
    display_name = 'Cerebras'
    base_url = 'https://api.cerebras.ai/v1'
    validation_model = 'llama3.1-8b'
