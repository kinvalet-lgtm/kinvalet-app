"""Extraction contracts."""
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class ExtractionResultDTO(BaseModel):
    id: UUID
    inbound_message_id: UUID
    confidence_score: float
    requires_human_review: bool
    extracted_json: dict


class ExtractionAPI(Protocol):
    async def get_result(self, extraction_result_id: UUID) -> Optional[ExtractionResultDTO]: ...
