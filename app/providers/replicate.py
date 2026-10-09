from .base import LLMProvider

class ReplicateProvider(LLMProvider):
    provider_id = 'replicate'
    display_name = 'Replicate'
    base_url = 'https://openai-proxy.replicate.com/v1'
    validation_model = 'meta/meta-llama-3.1-8b-instruct'
