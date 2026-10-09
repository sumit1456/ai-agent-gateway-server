from .base import LLMProvider

class GoogleProvider(LLMProvider):
    provider_id = 'google'
    display_name = 'Google Gemini'
    base_url = 'https://generativelanguage.googleapis.com/v1beta/openai/'
    validation_model = 'gemini-1.5-flash'
