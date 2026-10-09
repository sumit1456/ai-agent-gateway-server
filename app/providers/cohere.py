from .base import LLMProvider

class CohereProvider(LLMProvider):
    provider_id = 'cohere'
    display_name = 'Cohere'
    base_url = 'https://api.cohere.ai/compatibility/v1'
    validation_model = 'command-r-plus'
