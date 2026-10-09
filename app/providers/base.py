from abc import ABC
import httpx
from langchain_openai import ChatOpenAI

class LLMProvider(ABC):
    provider_id: str
    display_name: str
    base_url: str
    validation_model: str      # a cheap model id used for the key test call

    def get_chat_model(self, model: str, api_key: str, temperature: float = 0.3,
                       max_tokens: int = 2048) -> ChatOpenAI:
        # max_retries=0 on purpose: WE own retries (engine/retry.py).
        # stream_usage=True so token usage is returned while streaming.
        return ChatOpenAI(model=model, api_key=api_key, base_url=self.base_url,
                          temperature=temperature, max_tokens=max_tokens,
                          stream_usage=True, max_retries=0, timeout=60)

    def auth_headers(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    async def validate_api_key(self, api_key: str) -> bool:
        async with httpx.AsyncClient(timeout=20) as c:
            # 1. Fetch available models
            models_resp = await c.get(
                self.models_url(),
                headers=self.auth_headers(api_key)
            )
            return models_resp.status_code == 200