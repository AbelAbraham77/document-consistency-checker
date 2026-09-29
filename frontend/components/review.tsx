"use client";

import { useEffect, useState } from "react";
import { api, ClaimPage, errorMessage, Finding, FindingDetail, humanLabel, Progress } from "../lib/api";

export function Notice({ children, retry }: { children: React.ReactNode; retry?: () => void }) {
  return <div className="notice error" role="alert"><span>{children}</span>{retry && <button className="text-button" onClick={retry}>Try again</button>}</div>;
}

function FindingCard({ documentId, finding }: { documentId: string; finding: Finding }) {
  const [detail, setDetail] = useState<FindingDetail | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<FindingDetail>(`/documents/${documentId}/contradictions/${finding.id}`, { signal: controller.signal })
      .then(setDetail).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [documentId, finding.id, attempt]);
  const confidence = Number(finding.confidence);
  return <article className="finding">
    <div className="finding-heading"><h3><span className="alert-mark" aria-hidden="true">!</span>Potential inconsistency</h3><span className="badge amber">{humanLabel(finding.classification)}</span></div>
    {error ? <Notice retry={() => setAttempt(attempt + 1)}>{error}</Notice> : !detail ? <p className="loading" role="status">Loading original statements…</p> :
      <div className="comparison">
        <blockquote><span className="page-tag">Page {detail.claim_a.source_page}</span><p>{detail.claim_a.claim_text}</p></blockquote>
        <span className="versus">vs</span>
        <blockquote><span className="page-tag">Page {detail.claim_b.source_page}</span><p>{detail.claim_b.claim_text}</p></blockquote>
      </div>}
    <p className="explanation">{finding.explanation}</p>
    <div className="finding-meta"><span title="The verifier's reported confidence, not a guarantee of correctness">{Number.isFinite(confidence) ? `${Math.round(confidence * 100)}% confidence` : "Confidence unavailable"}</span><span>{finding.source === "gemini" ? "AI verified" : "Unverified by rules"}</span></div>
    {detail && <details className="context"><summary>View context <span aria-hidden="true">↗</span></summary><div className="context-grid">
      {[detail.claim_a, detail.claim_b].map((claim, index) => <section key={index}>
        <h4>{claim.context.section || "Source context"}</h4><p className="muted">{claim.context.page_start === claim.context.page_end ? `Page ${claim.context.page_start}` : `Pages ${claim.context.page_start}–${claim.context.page_end}`} · Statement on page {claim.source_page}</p>
        <pre>{claim.context.text}</pre>
      </section>)}
    </div></details>}
  </article>;
}

export function Findings({ documentId, status, progress, partial }: {
  documentId: string;
  status: string;
  progress: Progress | null;
  partial: boolean;
}) {
  const [items, setItems] = useState<Finding[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [visible, setVisible] = useState(10);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<Finding[]>(`/documents/${documentId}/contradictions`, { signal: controller.signal })
      .then(setItems).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [documentId, status, progress?.counts.verified_contradictions, attempt]);
  if (error) return <Notice retry={() => setAttempt(attempt + 1)}>{error}</Notice>;
  if (!items) return <p className="loading" role="status">Loading results…</p>;
  if (!items.length) {
    const active = !["COMPLETED", "FAILED", "CANCELLED"].includes(status);
    if (active) {
      const counts = progress?.counts;
      return <div className="empty" role="status">
        <div className="spinner" aria-hidden="true" />
        <h3>Processing page batches</h3>
        <p>Claims and potential contradictions will appear as the remaining pages are processed.</p>
        {counts && <p className="muted">Pages processed: {counts.pages_processed ?? 0} of {counts.pages}; claims extracted: {counts.claims}; candidates evaluated: {(counts.candidates_verified ?? 0) + (counts.candidates_failed ?? 0)}</p>}
      </div>;
    }
    return <div className="empty"><span className="empty-icon" aria-hidden="true">✓</span><h3>{partial ? "Review incomplete" : "No verified contradictions found"}</h3><p>{partial ? "Processing did not finish, so this result cannot confirm whether the document contains contradictions. Check the error above and retry." : "No verified contradictions were returned. You can still review the extracted claims below."}</p></div>;
  }
  return <><p className="section-note">Review each potential inconsistency in its original context. Results support your review; they are not a final determination.</p>
    <div className="findings">{items.slice(0, visible).map(finding => <FindingCard key={finding.id} documentId={documentId} finding={finding} />)}</div>
    {visible < items.length && <button className="secondary load-more" onClick={() => setVisible(visible + 10)}>Show more results ({items.length - visible} remaining)</button>}</>;
}

export function Claims({ documentId }: { documentId: string }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ClaimPage | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPage(null); setError("");
    api<ClaimPage>(`/documents/${documentId}/claims?offset=${offset}&limit=20`, { signal: controller.signal })
      .then(setPage).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [documentId, offset, attempt]);
  return <section aria-label="Extracted claims">
    <p className="section-note">The factual statements extracted from this document, with their original wording and source pages. <button className="text-button" onClick={() => setAttempt(value => value + 1)}>Refresh claims</button></p>
    {error ? <Notice retry={() => setAttempt(attempt + 1)}>{error}</Notice> : !page ? <p role="status" className="loading">Loading claims…</p> : page.total === 0 ?
      <div className="empty"><h3>No extracted claims yet</h3><p>Claims will appear here once extraction succeeds.</p></div> :
      <><div className="table-scroll" tabIndex={0} role="region" aria-label="Claims table"><table><thead><tr><th>Page</th><th>Original statement</th><th>Entity / metric</th><th>Value / unit</th><th>Period / scope</th></tr></thead>
        <tbody>{page.items.map(claim => <tr key={claim.id}><td><span className="page-tag">{claim.source_page}</span></td><td className="statement-cell">{claim.claim_text}</td>
          <td>{claim.entity_raw}<small>{claim.metric_normalized ? humanLabel(claim.metric_normalized) : claim.metric_raw}</small></td>
          <td>{claim.value_text ?? claim.value_numeric ?? "—"}<small>{claim.unit ?? "—"}</small></td>
          <td>{claim.period ?? "Not stated"}<small>{claim.scope ?? "Scope not stated"}{claim.qualifier ? ` · ${claim.qualifier}` : ""}</small></td></tr>)}</tbody></table></div>
      <div className="pagination"><span>{offset + 1}–{Math.min(offset + page.items.length, page.total)} of {page.total} claims</span><div><button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</button><button className="secondary" disabled={offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>Next</button></div></div></>}
  </section>;
}
