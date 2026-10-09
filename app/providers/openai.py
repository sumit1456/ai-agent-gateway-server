from .base import LLMProvider

class OpenAIProvider(LLMProvider):
    provider_id = 'openai'
    display_name = 'OpenAI'
    base_url = 'https://api.openai.com/v1'
    validation_model = 'gpt-4o-mini'
