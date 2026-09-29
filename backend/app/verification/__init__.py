"""Final evidence-based classification and persistence, initialized only on use."""

from app.verification.schemas import VerificationResult

__all__ = ["VerificationResult", "verify_candidate", "VerificationService", "candidate_from_claims"]


def __getattr__(name):
    if name == "candidate_from_claims":
        from app.verification.context import candidate_from_claims
        return candidate_from_claims
    if name in {"verify_candidate", "VerificationService"}:
        from app.verification import service
        return getattr(service, name)
    raise AttributeError(name)
