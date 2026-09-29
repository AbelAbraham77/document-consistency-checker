"""Select document storage without coupling API or pipeline code to a provider."""

from .base import DocumentStorage, StorageError
from .local import LocalDocumentStorage
from .supabase import SupabaseDocumentStorage


def create_document_storage(settings) -> DocumentStorage:
    if settings.document_storage == "local":
        return LocalDocumentStorage(settings.upload_dir)
    return SupabaseDocumentStorage(settings.supabase_url, settings.supabase_service_role_key.get_secret_value(),
                                   settings.supabase_storage_bucket, timeout=settings.storage_timeout_seconds,
                                   max_bytes=settings.max_upload_bytes)


__all__ = ["DocumentStorage", "LocalDocumentStorage", "SupabaseDocumentStorage", "StorageError", "create_document_storage"]
