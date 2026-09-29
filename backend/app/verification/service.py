"""Bounded final verification with source context and transactional persistence."""

import json
import logging
import time
import random
from app.llm_retries import LLMServiceError, retry_delay, error_detail

from sqlalchemy.orm import Session

from app.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.database.verification import save_verification_result
from app.extraction.providers.base import ClaimProvider, ProviderError
from app.providers.factory import text_provider
from app.extraction.settings import ExtractionSettings
from app.matching.models import CandidatePair
from app.verification.context import load_pair_context
from app.verification.prompts import INSTRUCTIONS
from app.verification.schemas import VerificationResult, validate_verification_response, verification_schema
from app.verification.settings import VerificationSettings

logger = logging.getLogger(__name__)


def rule_based_verifier(candidate: CandidatePair) -> VerificationResult:
    """Return a conservative verdict using only normalized claim text and fields."""
    import re

    a, b = candidate.claim_a, candidate.claim_b
    same_subject = (
        a.entity_normalized == b.entity_normalized
        and a.metric_normalized == b.metric_normalized
    )
    text_a, text_b = a.original.claim_text.lower(), b.original.claim_text.lower()
    value_a, value_b = a.original.value_numeric, b.original.value_numeric
    period_a = a.period.canonical if a.period is not None else a.original.period
    period_b = b.period.canonical if b.period is not None else b.original.period
    negation = re.compile(r"\b(?:not|no|never|without|failed to)\b|n't")
    neg_a, neg_b = bool(negation.search(text_a)), bool(negation.search(text_b))
    opposites = (("increase", "decrease"), ("approved", "rejected"), ("before", "after"),
                 ("true", "false"), ("allowed", "prohibited"), ("above", "below"))
    # Semantic candidates can contain related claims with different metrics.
    # Do not turn those preliminary matches into contradictions.
    if not same_subject:
        return VerificationResult(classification="UNCERTAIN", confidence=0.4,
            explanation="Unverified by rules; the statements need an AI check.", source="rules")
    if value_a is not None and value_b is not None and value_a != value_b:
        return VerificationResult(classification="CONTRADICTION", confidence=0.95,
            explanation="The same metric has different numbers.", source="rules")
    if period_a != period_b and period_a is not None and period_b is not None:
        return VerificationResult(classification="CONTRADICTION", confidence=0.95,
            explanation="The same metric has different reporting dates.", source="rules")
    if neg_a != neg_b:
        without_negation = re.sub(r"\b(?:not|no|never|without|failed to)\b|n't", "", text_a)
        other_without_negation = re.sub(r"\b(?:not|no|never|without|failed to)\b|n't", "", text_b)
        if without_negation.split() == other_without_negation.split():
            return VerificationResult(classification="CONTRADICTION", confidence=0.9,
                explanation="The statements differ by negation.", source="rules")
    if any((left in text_a and right in text_b) or (right in text_a and left in text_b)
           for left, right in opposites):
        return VerificationResult(classification="CONTRADICTION", confidence=0.9,
            explanation="The statements contain opposite terms.", source="rules")
    if value_a == value_b and period_a == period_b:
        return VerificationResult(classification="CONSISTENT", confidence=0.9,
            explanation="The statements contain the same metric, value, and reporting date.", source="rules")
    if a.original.value_text == b.original.value_text:
        return VerificationResult(classification="CONSISTENT", confidence=0.9,
            explanation="The statements contain the same metric and value.", source="rules")
    return VerificationResult(classification="UNCERTAIN", confidence=0.4,
        explanation="Unverified by rules; the statements need an AI check.", source="rules")


class VerificationError(LLMServiceError):
    """A provider/validation failure, never silently converted into a verdict."""


class VerificationService:
    def __init__(self, session: Session, provider: ClaimProvider, *, max_retries: int = 2,
                 max_context_characters: int = 120000, sleep=time.sleep, random_value=random.random):
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        if type(max_context_characters) is not int or max_context_characters < 1000:
            raise ValueError("max_context_characters must be at least 1000")
        self.session, self.provider = session, provider
        self.max_retries, self.max_context_characters, self.sleep = max_retries, max_context_characters, sleep
        self.random_value = random_value

    def verify_batch(self, candidates, *, on_result, on_request):
        """Commit each usable verdict via callback; retry only invalid/missing items once.

        A quota error escapes immediately. Already delivered results survive it.
        The caller owns the durable queue and the per-run request budget.
        """
        from collections import Counter
        from app.verification.schemas import BatchVerificationItem, BatchVerificationResponse
        pending = {}
        insufficient_context = 0
        for candidate_id, pair in candidates.items():
            context = load_pair_context(self.session, pair)
            evidence = json.dumps(context.evidence, ensure_ascii=False)
            reason = context.insufficient_reason
            if len(evidence) > self.max_context_characters:
                reason = "Complete source context exceeds the verification limit."
            if reason:
                insufficient_context += 1
                on_result(candidate_id, VerificationResult(classification="INSUFFICIENT_CONTEXT",
                                                          explanation=reason, confidence=0.0))
            else:
                pending[candidate_id] = context.evidence
        # The pipeline caps this mapping to the top ten uncertain candidates.
        groups = [pending] if pending else []
        for group in groups:
            for attempt in range(1):
                instructions = INSTRUCTIONS + "\nVerify each candidate independently. Return {results: [{candidate_id, classification, explanation, confidence}]}. Use exactly the supplied candidate IDs."
                try:
                    if hasattr(self.provider, "before_request"):
                        self.provider.before_request = on_request
                    else:
                        on_request()
                    raw = self.provider.generate(instructions=instructions,
                        evidence=json.dumps({"candidates": [{"candidate_id": key, "evidence": value}
                                            for key, value in group.items()]}, ensure_ascii=False),
                        schema=BatchVerificationResponse.model_json_schema())
                except ProviderError as exc:
                    if exc.category in {"missing_structured_output", "invalid_provider_response", "incomplete_output"}:
                        raw = ""  # Apply the same single schema-correction retry.
                    else:
                        raise VerificationError("Final verification provider unavailable.", category=exc.category,
                            retryable=exc.retryable, attempts=1, http_status=exc.http_status,
                            provider=getattr(self.provider, "provider_name", None)) from None
                accepted = []
                try:
                    def unique(items):
                        result = {}
                        for key, value in items:
                            if key in result:
                                raise ValueError("Duplicate JSON field")
                            result[key] = value
                        return result
                    body = json.loads(raw, object_pairs_hook=unique)
                    items = body["results"] if isinstance(body, dict) and set(body) == {"results"} else []
                    if not isinstance(items, list):
                        items = []
                    counts = Counter(item.get("candidate_id") for item in items
                                     if isinstance(item, dict) and isinstance(item.get("candidate_id"), str))
                    for item in items:
                        try:
                            verdict = BatchVerificationItem.model_validate(item)
                        except (ValueError, TypeError):
                            continue
                        if verdict.candidate_id in group and counts[verdict.candidate_id] == 1:
                            accepted.append(verdict)
                except (ValueError, TypeError, KeyError):
                    pass
                for verdict in accepted:
                    on_result(verdict.candidate_id, VerificationResult.model_validate(
                        verdict.model_dump(exclude={"candidate_id"})))
                    del group[verdict.candidate_id]
                logger.info("Batch verification response: requested=%d accepted=%d rejected=%d",
                            len(group) + len(accepted), len(accepted), len(group))
                if not group:
                    break
            for candidate_id in group:
                on_result(candidate_id, None)
        logger.info("Batch verification classifications: insufficient_context=%d", insufficient_context)

    def verify_candidate(self, candidate: CandidatePair) -> VerificationResult:
        context = load_pair_context(self.session, candidate)
        evidence = json.dumps(context.evidence, ensure_ascii=False)
        reason = context.insufficient_reason
        if len(evidence) > self.max_context_characters:
            reason = "The complete source context exceeds the verification limit; critical evidence was not truncated."
        if reason:
            result = VerificationResult(classification="INSUFFICIENT_CONTEXT", explanation=reason, confidence=0.0)
        else:
            result = self._verify(evidence)
        save_verification_result(self.session, document_id=context.claim_a.document_id,
            claim_a_id=context.claim_a.id, claim_b_id=context.claim_b.id, result=result)
        logger.info("Verification recorded: classification=%s", result.classification)
        if result.classification in {"CONSISTENT", "UNRELATED"}:
            logger.debug("Verification dismissed a non-contradictory pair")
        return result

    def _verify(self, evidence: str) -> VerificationResult:
        for attempt in range(self.max_retries + 1):
            http_status, retry_after = None, None
            try:
                raw = self.provider.generate(instructions=INSTRUCTIONS, evidence=evidence, schema=verification_schema())
                return validate_verification_response(raw)
            except ProviderError as exc:
                category, retryable = exc.category, exc.retryable
                http_status, retry_after = exc.http_status, exc.retry_after
            except (ValueError, TypeError):
                category, retryable = "invalid_verification_output", True
            logger.warning("Verification failed: attempt=%d category=%s", attempt + 1, category)
            if not retryable or attempt == self.max_retries:
                raise VerificationError(f"Verification failed after {attempt + 1} attempt(s): {error_detail(category)}",
                    category=category, retryable=retryable, attempts=attempt+1, http_status=http_status,
                    provider=getattr(self.provider, "provider_name", None)) from None
            self.sleep(retry_delay(attempt, retry_after=retry_after, random_value=self.random_value))
        raise AssertionError("unreachable")


def verify_candidate(candidate: CandidatePair) -> VerificationResult:
    settings = VerificationSettings()
    # Reuse the tested structured-output transport, not the claim extraction service/prompt.
    provider_settings = ExtractionSettings(
        openai_api_key=settings.openai_api_key,
        extraction_timeout_seconds=settings.verification_timeout_seconds,
        extraction_max_output_tokens=settings.verification_max_output_tokens,
    )
    provider = text_provider(provider_settings, response_name="contradiction_verification",
                             model=settings.model or None)
    engine = create_database_engine(Settings())
    if engine is None:
        raise ValueError("Set DATABASE_URL before persisting verification results")
    try:
        with create_session_factory(engine).begin() as session:
            return VerificationService(session, provider, max_retries=settings.verification_max_retries,
                max_context_characters=settings.verification_max_context_characters).verify_candidate(candidate)
    finally:
        engine.dispose()
