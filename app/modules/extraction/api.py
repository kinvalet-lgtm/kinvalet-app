"""Extraction module public API."""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.extraction import ExtractionAPI, ExtractionResultDTO
from app.modules.extraction.models import ExtractionResult


class ExtractionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_result(self, extraction_result_id: uuid.UUID) -> Optional[ExtractionResultDTO]:
        result = await self._session.execute(
            select(ExtractionResult).where(ExtractionResult.id == extraction_result_id)
        )
        er = result.scalar_one_or_none()
        if er is None:
            return None
        return ExtractionResultDTO(
            id=er.id,
            inbound_message_id=er.inbound_message_id,
            confidence_score=float(er.confidence_score),
            requires_human_review=er.requires_human_review,
            extracted_json=er.extracted_json,
        )
