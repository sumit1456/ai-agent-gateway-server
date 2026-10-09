from .base import LLMProvider

class GroqProvider(LLMProvider):
    provider_id = 'groq'
    display_name = 'Groq'
    base_url = 'https://api.groq.com/openai/v1'
    validation_model = 'llama-3.1-8b-instant'
