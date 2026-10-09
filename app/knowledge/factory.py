from app.knowledge.embedder import Embedder
from app.knowledge.retriever import KnowledgeRetriever

def create_embedder() -> Embedder:
    """Factory function to create an embedder instance."""
    return Embedder()

def create_retriever() -> KnowledgeRetriever:
    """Factory function to create a retriever instance."""
    return KnowledgeRetriever()