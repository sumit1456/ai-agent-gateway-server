from .base import LLMProvider

class NvidiaProvider(LLMProvider):
    provider_id = "nvidia"
    display_name = "NVIDIA NIM"
    base_url = "https://integrate.api.nvidia.com/v1"
    validation_model = "meta/llama-3.1-8b-instruct"          # verify it exists