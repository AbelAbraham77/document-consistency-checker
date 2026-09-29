"""Claim extraction contracts and service; importing does not make LLM calls."""

from app.extraction.schemas import Claim, ClaimExtractionResponse
from app.extraction.validation import validate_claim_extraction_response
from app.extraction.service import extract_claims

__all__ = ["Claim", "ClaimExtractionResponse", "validate_claim_extraction_response", "extract_claims"]
