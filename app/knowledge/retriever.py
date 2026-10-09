import asyncio
from typing import List, Optional
from pydantic import BaseModel
from app.knowledge.embedder import Embedder
from app.config import settings
import logging

log = logging.getLogger(__name__)

class Chunk(BaseModel):
    id: str
    text: str
    score: float
    source: str = "unknown"

class KnowledgeRetriever:
    def __init__(self, api_key: Optional[str] = None, index_host: Optional[str] = None, rerank_model: Optional[str] = None):
        self.embedder = Embedder()
        self.api_key = api_key or settings.pinecone_api_key
        self.index_host = index_host or settings.pinecone_index_host
        self.rerank_model = rerank_model or settings.rerank_model
        self._pc = None
        self._index = None

    def _get_index(self):
        if not self._index and self.api_key and self.index_host:
            try:
                from pinecone import Pinecone
                self._pc = Pinecone(api_key=self.api_key)
                self._index = self._pc.Index(host=self.index_host)
            except Exception as e:
                log.warning(f"Could not connect to Pinecone index: {e}")
        return self._index

    async def search(self, namespaces: List[str], query: str, top_k: int = 20, final_k: int = 5) -> List[dict]:
        """Search the knowledge base for relevant documents across namespaces with reranking."""
        index = self._get_index()
        if not index:
            log.warning("Pinecone index not configured for KnowledgeRetriever")
            return []

        try:
            qvec = await asyncio.to_thread(self.embedder.embed_query, query)

            async def query_ns(ns: str):
                res = await asyncio.to_thread(
                    index.query, vector=qvec, top_k=top_k, namespace=ns, include_metadata=True
                )
                return getattr(res, "matches", []) or []

            batch = await asyncio.gather(*[query_ns(ns) for ns in namespaces])
            matches = [m for m_list in batch for m in m_list]
            matches.sort(key=lambda m: getattr(m, "score", 0.0), reverse=True)
            matches = matches[:top_k]

            chunks = [
                Chunk(
                    id=str(getattr(m, "id", "")),
                    text=(getattr(m, "metadata", {}) or {}).get("text", ""),
                    score=float(getattr(m, "score", 0.0)),
                    source=(getattr(m, "metadata", {}) or {}).get("source", "unknown")
                )
                for m in matches
            ]

            # Rerank if rerank_model and pinecone client available
            if self._pc and len(chunks) > 1 and self.rerank_model:
                try:
                    res = await asyncio.to_thread(
                        self._pc.inference.rerank,
                        model=self.rerank_model,
                        query=query,
                        documents=[c.text for c in chunks],
                        top_n=final_k,
                        return_documents=False
                    )
                    chunks = [chunks[r.index].model_copy(update={"score": r.score}) for r in res.data]
                except Exception as exc:
                    log.warning(f"Rerank failed ({exc}); using vector order")

            return [c.model_dump() for c in chunks[:final_k]]
        except Exception as exc:
            log.error(f"Error searching knowledge base: {exc}")
            return []

    async def add_documents(self, namespace: str, documents: List[dict]) -> None:
        """Add documents to a knowledge base namespace."""
        index = self._get_index()
        if not index:
            log.warning("Pinecone index not configured for add_documents")
            return

        try:
            texts = [d.get("text", "") for d in documents]
            embeddings = await asyncio.to_thread(self.embedder.embed_documents, texts)
            vectors = [
                {
                    "id": d.get("id", str(i)),
                    "values": embeddings[i],
                    "metadata": {
                        "text": d.get("text", ""),
                        "source": d.get("source", "unknown"),
                        "doc_id": d.get("doc_id", ""),
                    }
                }
                for i, d in enumerate(documents)
            ]
            for i in range(0, len(vectors), 100):
                await asyncio.to_thread(index.upsert, vectors=vectors[i:i + 100], namespace=namespace)
        except Exception as exc:
            log.error(f"Error adding documents to knowledge base: {exc}")
            raise

    async def delete_collection(self, namespace: str) -> None:
        """Delete a knowledge base collection namespace."""
        index = self._get_index()
        if not index:
            return
        try:
            await asyncio.to_thread(index.delete, delete_all=True, namespace=namespace)
        except Exception as exc:
            log.error(f"Error deleting knowledge base namespace: {exc}")
            raise