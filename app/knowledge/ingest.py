"""
Document ingestion pipeline.

Flow:
  1. Mark KbDocument status → ingesting
  2. Decode bytes based on content type (text / PDF / etc.)
  3. Split into chunks via RecursiveCharacterTextSplitter
  4. Embed + upsert chunks to Pinecone via KnowledgeRetriever
  5. Mark KbDocument status → ready (or failed)
"""
from __future__ import annotations
import asyncio, logging, uuid
from app.models.tables import KbDocument, KnowledgeBase
from app.db import get_db, SessionLocal
from app.knowledge.retriever import KnowledgeRetriever
from app.security import decrypt
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlmodel import select

log = logging.getLogger(__name__)

CHUNK_SIZE    = 512
CHUNK_OVERLAP = 64


async def _set_status(doc_id: str, status: str, chunk_count: int = 0, error: str | None = None) -> None:
    async with SessionLocal() as db:
        doc = await db.get(KbDocument, uuid.UUID(doc_id))
        if doc:
            doc.status = status
            if chunk_count:
                doc.chunk_count = chunk_count
            if error:
                doc.error = error
            db.add(doc)
            await db.commit()


def _decode_content(filename: str, content: bytes) -> str:
    """Best-effort text extraction from raw bytes."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            log.warning("PDF extraction failed (%s), falling back to raw bytes decode: %s", filename, exc)
    # Default: treat as UTF-8 text
    return content.decode("utf-8", errors="replace")


async def ingest_document(doc_id: str, filename: str, content: bytes, metadata: dict) -> None:
    """Ingest a document into its knowledge base namespace on Pinecone."""
    await _set_status(doc_id, "ingesting")

    try:
        # 1. Look up which KB this document belongs to and get its Pinecone creds
        async with SessionLocal() as db:
            doc = await db.get(KbDocument, uuid.UUID(doc_id))
            if not doc:
                raise ValueError(f"KbDocument {doc_id} not found")
            kb = await db.get(KnowledgeBase, doc.kb_id)
            if not kb:
                raise ValueError(f"KnowledgeBase for doc {doc_id} not found")
            namespace       = kb.namespace
            vector_api_key  = decrypt(kb.vector_api_key_enc) if kb.vector_api_key_enc else None
            vector_index_host = kb.vector_index_host
            rerank_model    = kb.rerank_model

        # 2. Decode content
        text = await asyncio.to_thread(_decode_content, filename, content)
        if not text.strip():
            raise ValueError("Document is empty or could not be extracted")

        # 3. Split into chunks
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )
        chunks = await asyncio.to_thread(splitter.split_text, text)
        if not chunks:
            raise ValueError("Text splitter produced no chunks")

        # 4. Build document dicts for the retriever
        source = metadata.get("source", filename)
        documents = [
            {
                "id":     f"{doc_id}-{i}",
                "text":   chunk,
                "source": source,
                "doc_id": doc_id,
            }
            for i, chunk in enumerate(chunks)
        ]

        # 5. Embed + upsert via KnowledgeRetriever
        retriever = KnowledgeRetriever(
            api_key=vector_api_key,
            index_host=vector_index_host,
            rerank_model=rerank_model,
        )
        await retriever.add_documents(namespace, documents)

        # 6. Mark ready
        await _set_status(doc_id, "ready", chunk_count=len(chunks))
        log.info("Ingested %d chunks from %s into namespace %s", len(chunks), filename, namespace)

    except Exception as exc:
        log.error("Ingestion failed for doc %s (%s): %s", doc_id, filename, exc)
        await _set_status(doc_id, "failed", error=str(exc))
        raise