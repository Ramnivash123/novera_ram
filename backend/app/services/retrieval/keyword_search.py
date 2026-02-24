"""
Keyword-based search using PostgreSQL full-text search.
Implements BM25-like ranking for exact match queries.
"""
from typing import List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, or_
from loguru import logger

from app.models.document import Chunk, Document


class KeywordSearchService:
    """
    Service for keyword-based search using PostgreSQL full-text search.
    Complements vector search for exact matches and specific terms.
    """

    def __init__(self):
        self.default_top_k = 10

    def _safe_extract_metadata(self, metadata_obj: Any) -> dict:
        """
        Safely extract metadata from SQLAlchemy object or dict.
        """
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

    async def search_keywords(
        self,
        query: str,
        db: AsyncSession,
        top_k: Optional[int] = None,
        doc_type: Optional[str] = None,
        department: Optional[str] = None,
        document_filter: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search chunks using keyword matching with optional document filtering.
        """
        k = top_k or self.default_top_k

        try:
            # Build query using PostgreSQL full-text search
            query_stmt = (
                select(
                    Chunk,
                    Document,
                    func.ts_rank_cd(
                        func.to_tsvector('english', Chunk.content),
                        func.plainto_tsquery('english', query)
                    ).label('rank')
                )
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Document.status == "completed",
                    func.to_tsvector('english', Chunk.content).op('@@')(
                        func.plainto_tsquery('english', query)
                    )
                )
            )

            # Apply filters
            if doc_type:
                query_stmt = query_stmt.where(Document.doc_type == doc_type)

            if department:
                query_stmt = query_stmt.where(Document.department == department)

            # Filter by document names
            if document_filter:
                doc_conditions = [
                    Document.filename.ilike(f'%{doc_name}%')
                    for doc_name in document_filter
                ]
                query_stmt = query_stmt.where(or_(*doc_conditions))
                logger.info(f"  Keyword search filtering to: {document_filter}")

            # Order by rank
            query_stmt = query_stmt.order_by(text('rank DESC')).limit(k)

            # Execute
            result = await db.execute(query_stmt)
            rows = result.all()

            # Format results - FIXED: Safe metadata extraction
            chunks = []
            for chunk, document, rank in rows:
                chunk_metadata = self._safe_extract_metadata(chunk.chunk_metadata)

                chunk_dict = {
                    'chunk_id': str(chunk.id),
                    'id': str(chunk.id),
                    'document_id': str(document.id),
                    'document_name': document.filename,
                    'content': chunk.content,
                    'chunk_type': chunk.chunk_type,
                    'chunk_index': chunk.chunk_index,
                    'page_numbers': chunk.page_numbers,
                    'section_title': chunk.section_title,
                    'token_count': chunk.token_count,
                    'keyword_score': float(rank),
                    'chunk_metadata': chunk_metadata,
                    'metadata': {
                        'document_title': document.filename,
                        'doc_type': document.doc_type,
                        'department': document.department,
                        **chunk_metadata,
                    }
                }

                chunks.append(chunk_dict)

            logger.info(f"Keyword search found {len(chunks)} matching chunks")

            return chunks

        except Exception as e:
            logger.error(f"Keyword search failed: {str(e)}", exc_info=True)
            return []

    async def search_exact_phrase(
        self,
        phrase: str,
        db: AsyncSession,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for exact phrase matches.
        """
        k = top_k or self.default_top_k

        try:
            query = (
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Document.status == "completed",
                    Chunk.content.ilike(f'%{phrase}%')
                )
                .limit(k)
            )

            result = await db.execute(query)
            rows = result.all()

            chunks = []
            for chunk, document in rows:
                chunk_metadata = self._safe_extract_metadata(chunk.chunk_metadata)

                chunk_dict = {
                    'chunk_id': str(chunk.id),
                    'id': str(chunk.id),
                    'document_id': str(document.id),
                    'document_name': document.filename,
                    'content': chunk.content,
                    'chunk_type': chunk.chunk_type,
                    'page_numbers': chunk.page_numbers,
                    'section_title': chunk.section_title,
                    'exact_match': True,
                    'chunk_metadata': chunk_metadata,
                    'metadata': {
                        'document_title': document.filename,
                        'doc_type': document.doc_type,
                        **chunk_metadata,
                    }
                }
                chunks.append(chunk_dict)

            logger.info(f"Exact phrase search found {len(chunks)} chunks")

            return chunks

        except Exception as e:
            logger.error(f"Exact phrase search failed: {str(e)}", exc_info=True)
            return []

    async def search_by_metadata(
        self,
        metadata_filters: Dict[str, Any],
        db: AsyncSession,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Search chunks by metadata fields.
        """
        k = top_k or self.default_top_k

        try:
            query = (
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Document.status == "completed")
            )

            for key, value in metadata_filters.items():
                query = query.where(
                    Chunk.chunk_metadata[key].astext == str(value)
                )

            query = query.limit(k)

            result = await db.execute(query)
            rows = result.all()

            chunks = []
            for chunk, document in rows:
                chunk_metadata = self._safe_extract_metadata(chunk.chunk_metadata)

                chunk_dict = {
                    'chunk_id': str(chunk.id),
                    'id': str(chunk.id),
                    'document_id': str(document.id),
                    'document_name': document.filename,
                    'content': chunk.content,
                    'chunk_type': chunk.chunk_type,
                    'chunk_metadata': chunk_metadata,
                    'metadata': {
                        'document_title': document.filename,
                        **chunk_metadata,
                    }
                }
                chunks.append(chunk_dict)

            return chunks

        except Exception as e:
            logger.error(f"Metadata search failed: {str(e)}", exc_info=True)
            return []


# Global instance
keyword_search_service = KeywordSearchService()

__all__ = ['KeywordSearchService', 'keyword_search_service']
