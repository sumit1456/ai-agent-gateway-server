from app.config import settings
from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings

class Embedder:
    def __init__(self):
        self.embeddings = NVIDIAEmbeddings(
            model=settings.embedding_model,
            # In a real implementation, we would need to handle the API key properly
            # This is simplified for the example
        )

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query text."""
        return self.embeddings.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple documents."""
        return self.embeddings.embed_documents(texts)