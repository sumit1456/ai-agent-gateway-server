from .base import LLMProvider

class AnthropicProvider(LLMProvider):
    provider_id = 'anthropic'
    display_name = 'Anthropic'
    base_url = 'https://api.anthropic.com/v1'
    validation_model = 'claude-3-5-haiku-20241022'

    def auth_headers(self, api_key: str) -> dict:
        # Anthropic's native API expects x-api-key; the OpenAI-compat layer
        # accepts Authorization. Send both so /models works either way.
        return {
            "Authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    async def validate_api_key(self, api_key: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=20) as c:
            # 1. Fetch available models
            models_resp = await c.get(
                self.models_url(),
                headers=self.auth_headers(api_key)
            )
            return models_resp.status_code == 200
