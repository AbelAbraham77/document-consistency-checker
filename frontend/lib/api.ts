
export type DocumentInfo = { id: string; filename: string; page_count: number; status: string };
export type Progress = {
  document_id: string; status: string; stage: string;
  counts: { pages: number; pages_processed?: number; chunks: number; chunks_completed: number; chunks_failed: number; claims: number;
    locally_extracted_facts: number; candidates_before_filtering: number; candidates_after_filtering: number;
    gemini_requests: number; candidates_verified: number; candidates_pending: number; candidates_failed: number;
    deterministic_candidates: number; semantic_candidates: number; verified_contradictions: number };
  errors: { stage: string; item_id: string; error_type: string; message: string; category?: string;
    http_status?: number; provider?: "gemini" | "openai" | null }[];
};
export type Claim = {
  id: string; entity_raw: string; entity_normalized: string | null; metric_raw: string; metric_normalized: string | null;
  value_text: string | null; value_numeric: string | number | null; unit: string | null; period: string | null;
  scope: string | null; qualifier: string | null; claim_text: string; source_page: number;
};
export type ClaimPage = { items: Claim[]; total: number; offset: number; limit: number };
export type Finding = { id: string; classification: string; explanation: string; confidence: string | number; source: "rules" | "gemini" };
export type ClaimContext = Claim & { context: { text: string; section: string | null; page_start: number; page_end: number } };
export type FindingDetail = Finding & { claim_a: ClaimContext; claim_b: ClaimContext };
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class ApiError extends Error {
  constructor(message: string, public status: number, public documentId?: string) { super(message); }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, cache: "no-store" });
  let data: unknown;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    let message = response.status === 404 ? "This document or result could not be found." :
      response.status >= 500 ? "The service is unavailable. Please try again shortly." : "The request could not be completed.";
    let documentId: string | undefined;
    if (typeof detail === "string") message = detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const error = detail as { message?: unknown; document_id?: unknown };
      if (typeof error.message === "string") message = error.message;
      if (typeof error.document_id === "string" && uuidPattern.test(error.document_id)) documentId = error.document_id;
    }
    throw new ApiError(message, response.status, documentId);
  }
  if (data === null) throw new ApiError("The service returned an unreadable response. Please try again.", response.status);
  return data as T;
}
export function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Unable to reach the service. Check your connection and try again.";
}
export const isFinished = (status: string) => ["COMPLETED", "COMPLETED_WITH_PENDING_VERIFICATION", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"].includes(status);
export const humanLabel = (value: string) => value.toLowerCase().replaceAll("_", " ");
