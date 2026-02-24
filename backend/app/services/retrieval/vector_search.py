"""
Vector similarity search using pgvector.
Implements cosine similarity search with filtering and ranking.
"""
from typing import List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, desc, literal_column
from loguru import logger

from app.models.document import Chunk, Document
from app.core.config import settings


class VectorSearchService:
    """
    Service for semantic similarity search using vector embeddings.
    Leverages pgvector's cosine similarity for efficient retrieval.
    """

    def __init__(self):
        self.top_k = settings.retrieval_top_k
        self.similarity_threshold = settings.similarity_threshold

    def _safe_extract_metadata(self, metadata_obj: Any) -> dict:
        """Safely extract metadata from SQLAlchemy object or dict."""
        if metadata_obj is None:
            return {}
        if isinstance(metadata_obj, dict):
            return metadata_obj
        if hasattr(metadata_obj, '__dict__'):
            try:
                return {k: v for k, v in metadata_obj.__dict__.items() if not k.startswith('_')}
            except Exception:
                pass
        if hasattr(metadata_obj, 'keys') and hasattr(metadata_obj, '__getitem__'):
            try:
                return {k: metadata_obj[k] for k in metadata_obj.keys()}
            except Exception:
                pass
        logger.warning(f"Could not extract metadata from {type(metadata_obj)}")
        return {}

    async def search_similar_chunks(
        self,
        query_embedding: List[float],
        db: AsyncSession,
        top_k: Optional[int] = None,
        doc_type: Optional[str] = None,
        department: Optional[str] = None,
        document_ids: Optional[List[UUID]] = None,
        document_filter: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search for chunks similar to query embedding with document filtering."""
        k = top_k or self.top_k

        logger.info(f"Vector search: top_k={k}, threshold={self.similarity_threshold}")
        logger.info(f"Query embedding dimensions: {len(query_embedding)}")

        try:
            # Convert embedding to string format for raw SQL
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            # Use raw SQL to avoid SQLAlchemy string formatting issues with pgvector
            # This is the most reliable way to pass vector parameters
            sql_parts = [
                """
                SELECT 
                    c.id as chunk_id,
                    c.document_id,
                    c.content,
                    c.chunk_type,
                    c.chunk_index,
                    c.page_numbers,
                    c.section_title,
                    c.token_count,
                    c.metadata as chunk_metadata,
                    d.id as doc_id,
                    d.filename,
                    d.doc_type,
                    d.department,
                    d.metadata as doc_metadata,
                    1 - (c.embedding <=> :embedding::vector) as similarity
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE d.status = 'completed'
                """
            ]

            params = {"embedding": embedding_str}

            if doc_type:
                sql_parts.append("AND d.doc_type = :doc_type")
                params["doc_type"] = doc_type

            if department:
                sql_parts.append("AND d.department = :department")
                params["department"] = department

            if document_ids:
                placeholders = ", ".join(f":doc_id_{i}" for i in range(len(document_ids)))
                sql_parts.append(f"AND d.id IN ({placeholders})")
                for i, did in enumerate(document_ids):
                    params[f"doc_id_{i}"] = str(did)

            if document_filter:
                filter_conditions = []
                for i, doc_name in enumerate(document_filter):
                    filter_conditions.append(f"d.filename ILIKE :doc_filter_{i}")
                    params[f"doc_filter_{i}"] = f"%{doc_name}%"
                sql_parts.append(f"AND ({' OR '.join(filter_conditions)})")
                logger.info(f"  Filtering to documents matching: {document_filter}")

            sql_parts.append("ORDER BY similarity DESC")
            sql_parts.append(f"LIMIT {k * 2}")

            full_sql = "\n".join(sql_parts)

            # Execute raw query
            result = await db.execute(text(full_sql), params)
            rows = result.fetchall()

            # Format results
            chunks = []
            for row in rows:
                similarity = float(row.similarity)
                if similarity < self.similarity_threshold:
                    continue

                chunk_metadata = self._safe_extract_metadata(row.chunk_metadata)
                doc_metadata = self._safe_extract_metadata(row.doc_metadata)

                chunk_dict = {
                    'chunk_id': str(row.chunk_id),
                    'id': str(row.chunk_id),
                    'document_id': str(row.doc_id),
                    'document_name': row.filename,
                    'content': row.content,
                    'chunk_type': row.chunk_type,
                    'chunk_index': row.chunk_index,
                    'page_numbers': row.page_numbers,
                    'section_title': row.section_title,
                    'token_count': row.token_count,
                    'similarity_score': similarity,
                    'chunk_metadata': chunk_metadata,
                    'metadata': {
                        'document_title': row.filename,
                        'doc_type': row.doc_type,
                        'department': row.department,
                        **chunk_metadata,
                    }
                }

                chunks.append(chunk_dict)

            logger.info(f"Vector search found {len(chunks)} chunks above threshold {self.similarity_threshold}")

            return chunks[:k]

        except Exception as e:
            logger.error(f"Vector search failed: {str(e)}", exc_info=True)
            return []

    async def search_by_document(
        self,
        query_embedding: List[float],
        document_id: UUID,
        db: AsyncSession,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Search within a specific document only."""
        return await self.search_similar_chunks(
            query_embedding=query_embedding,
            db=db,
            top_k=top_k,
            document_ids=[document_id]
        )

    async def get_chunk_neighbors(
        self,
        chunk_id: UUID,
        db: AsyncSession,
        n_before: int = 1,
        n_after: int = 1
    ) -> List[Dict[str, Any]]:
        """Get neighboring chunks for context expansion."""
        try:
            result = await db.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id == chunk_id)
            )
            row = result.first()

            if not row:
                logger.warning(f"Chunk {chunk_id} not found for neighbor expansion")
                return []

            target_chunk, document = row
            target_index = target_chunk.chunk_index

            query = (
                select(Chunk)
                .where(
                    Chunk.document_id == target_chunk.document_id,
                    Chunk.chunk_index >= target_index - n_before,
                    Chunk.chunk_index <= target_index + n_after
                )
                .order_by(Chunk.chunk_index)
            )

            result = await db.execute(query)
            chunks = result.scalars().all()

            neighbor_chunks = []
            for chunk in chunks:
                chunk_metadata = self._safe_extract_metadata(chunk.chunk_metadata)

                chunk_dict = {
                    'chunk_id': str(chunk.id),
                    'id': str(chunk.id),
                    'document_id': str(chunk.document_id),
                    'document_name': document.filename,
                    'content': chunk.content,
                    'chunk_type': chunk.chunk_type,
                    'chunk_index': chunk.chunk_index,
                    'page_numbers': chunk.page_numbers,
                    'section_title': chunk.section_title,
                    'token_count': chunk.token_count,
                    'is_target': chunk.id == chunk_id,
                    'chunk_metadata': chunk_metadata,
                    'metadata': {
                        'document_title': document.filename,
                        **chunk_metadata,
                    }
                }
                neighbor_chunks.append(chunk_dict)

            return neighbor_chunks

        except Exception as e:
            logger.error(f"Failed to get chunk neighbors: {str(e)}", exc_info=True)
            return []


# Global instance
vector_search_service = VectorSearchService()

__all__ = ['VectorSearchService', 'vector_search_service']
