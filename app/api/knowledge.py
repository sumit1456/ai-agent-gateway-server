from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from app.security import current_user, encrypt
from app.db import get_db
from app.models.tables import User, KnowledgeBase, KbDocument, Provider
from app.knowledge.ingest import ingest_document
from typing import List, Optional
import uuid

router = APIRouter()

class KnowledgeBaseCreate(BaseModel):
    name: str
    provider_id: str                      # UUID or provider name
    embedding_model: str
    vector_api_key: Optional[str] = None  # User's Pinecone API key (BYOD)
    vector_index_host: Optional[str] = None # User's Pinecone Index Host URL
    rerank_model: Optional[str] = "bge-reranker-v2-m3"

async def _resolve_provider(db, user_id, provider_ref: str) -> Provider | None:
    try:
        p_uuid = uuid.UUID(str(provider_ref))
        result = await db.exec(
            select(Provider).where(
                Provider.user_id == user_id,
                Provider.id == p_uuid
            )
        )
        p = result.first()
        if p:
            return p
    except (ValueError, TypeError):
        pass

    result = await db.exec(
        select(Provider).where(
            Provider.user_id == user_id,
            Provider.provider == str(provider_ref)
        )
    )
    return result.first()

@router.post("/", response_model=dict)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Create a new knowledge base with optional BYOD vector database credentials."""
    provider = await _resolve_provider(db, current_user.id, payload.provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embedding provider not found or not configured"
        )
    
    # Optional Pinecone credentials validation if provided
    vector_api_key_enc = None
    vector_index_host = None
    if payload.vector_api_key and payload.vector_index_host:
        key_str = payload.vector_api_key.strip()
        host_str = payload.vector_index_host.strip()
        if not key_str or not host_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Both Vector API Key and Index Host are required for BYOD vector database."
            )
        try:
            from pinecone import Pinecone
            pc = Pinecone(api_key=key_str)
            index = pc.Index(host=host_str)
            # Lightweight verification
            index.describe_index_stats()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not connect to Pinecone index: {str(e)}"
            )
        vector_api_key_enc = encrypt(key_str)
        vector_index_host = host_str

    kb = KnowledgeBase(
        user_id=current_user.id,
        name=payload.name,
        provider_id=provider.id,
        namespace=str(uuid.uuid4()),
        embedding_model=payload.embedding_model,
        vector_api_key_enc=vector_api_key_enc,
        vector_index_host=vector_index_host,
        rerank_model=payload.rerank_model or "bge-reranker-v2-m3"
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    
    return {
        "id": str(kb.id),
        "name": kb.name,
        "provider_id": str(kb.provider_id),
        "namespace": kb.namespace,
        "embedding_model": kb.embedding_model,
        "vector_index_host": kb.vector_index_host,
        "has_custom_vector_db": bool(kb.vector_api_key_enc),
        "rerank_model": kb.rerank_model,
        "created_at": kb.created_at.isoformat()
    }

@router.get("/", response_model=List[dict])
async def list_knowledge_bases(
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """List all knowledge bases for the current user."""
    result = await db.exec(
        select(KnowledgeBase).where(KnowledgeBase.user_id == current_user.id)
    )
    kbs = result.all()
    
    return [
        {
            "id": str(kb.id),
            "name": kb.name,
            "provider_id": str(kb.provider_id),
            "namespace": kb.namespace,
            "embedding_model": kb.embedding_model,
            "vector_index_host": kb.vector_index_host,
            "has_custom_vector_db": bool(kb.vector_api_key_enc),
            "rerank_model": kb.rerank_model,
            "created_at": kb.created_at.isoformat()
        }
        for kb in kbs
    ]

@router.get("/{kb_id}", response_model=dict)
async def get_knowledge_base(
    kb_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Get a specific knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid knowledge base ID format"
        )
    
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    return {
        "id": str(kb.id),
        "name": kb.name,
        "provider_id": str(kb.provider_id),
        "namespace": kb.namespace,
        "embedding_model": kb.embedding_model,
        "vector_index_host": kb.vector_index_host,
        "has_custom_vector_db": bool(kb.vector_api_key_enc),
        "rerank_model": kb.rerank_model,
        "created_at": kb.created_at.isoformat()
    }

@router.delete("/{kb_id}", response_model=dict)
async def delete_knowledge_base(
    kb_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Delete a knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid knowledge base ID format"
        )
    
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    # In a real implementation, we would also delete the Pinecone namespace
    # and all associated documents
    
    await db.delete(kb)
    await db.commit()
    
    return {
        "message": f"Knowledge base {kb_id} deleted successfully"
    }

@router.post("/{kb_id}/documents", response_model=dict)
async def upload_document(
    kb_id: str,
    filename: str,
    content: bytes,  # In practice, this would come from a file upload
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Upload and ingest a document into a knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid knowledge base ID format"
        )
    
    # Verify the knowledge base exists and belongs to the user
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    # Create document record
    doc = KbDocument(
        kb_id=kb.id,
        filename=filename,
        status="pending"
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    
    # Start ingestion process (in background)
    # In a real implementation, we would use background tasks
    await ingest_document(str(kb.id), filename, content, {})
    
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "status": doc.status,
        "created_at": doc.created_at.isoformat()
    }

@router.get("/{kb_id}/documents", response_model=List[dict])
async def list_documents(
    kb_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """List all documents in a knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid knowledge base ID format"
        )
    
    # Verify the knowledge base exists and belongs to the user
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    result = await db.exec(
        select(KbDocument).where(KbDocument.kb_id == kb.id)
    )
    documents = result.all()
    
    return [
        {
            "id": str(doc.id),
            "filename": doc.filename,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "created_at": doc.created_at.isoformat()
        }
        for doc in documents
    ]

@router.delete("/{kb_id}/documents/{doc_id}", response_model=dict)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Delete a document from a knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
        doc_uuid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format"
        )
    
    # Verify the knowledge base exists and belongs to the user
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    # Get the document
    result = await db.exec(
        select(KbDocument).where(
            KbDocument.id == doc_uuid,
            KbDocument.kb_id == kb.id
        )
    )
    doc = result.first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    await db.delete(doc)
    await db.commit()
    
    return {
        "message": f"Document {doc_id} deleted from knowledge base {kb_id}"
    }

@router.post("/{kb_id}/search", response_model=dict)
async def search_knowledge_base(
    kb_id: str,
    query: str,
    top_k: int = 5,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Search a knowledge base."""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid knowledge base ID format"
        )
    
    # Verify the knowledge base exists and belongs to the user
    result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id == kb_uuid
        )
    )
    kb = result.first()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found"
        )
    
    # In a real implementation, we would use the retriever to search
    # For now, we'll return a placeholder
    return {
        "query": query,
        "results": [],  # Placeholder
        "message": "Knowledge search not fully implemented"
    }