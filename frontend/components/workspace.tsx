"use client";

import { useEffect, useState } from "react";
import { api, DocumentInfo, errorMessage, humanLabel, isFinished, Progress } from "../lib/api";
import { Claims, Findings, Notice } from "./review";

const steps = ["Parsing", "Chunking", "Extracting claims", "Matching claims", "Verifying candidates", "Completed"];
const stageIndex: Record<string, number> = { UPLOADED: 0, QUEUED: 0, PARSING: 0, CHUNKING: 1, EXTRACTING_CLAIMS: 2, NORMALIZING: 3, GENERATING_CANDIDATES: 3, EMBEDDING: 3, SEMANTIC_RETRIEVAL: 3, VERIFYING: 4, COMPLETED: 5, COMPLETED_WITH_WARNINGS: 5, COMPLETED_WITH_PENDING_VERIFICATION: 5 };

function failureAdvice(progress: Progress | null): string {
  const error = progress?.errors.find(item => item.stage === "EXTRACTING_CLAIMS") ?? progress?.errors[0];
  if (!error) return "Check the processing details and retry when the service is available.";
  const provider = error.provider === "gemini" ? "Gemini" : error.provider === "openai" ? "OpenAI" : "The AI provider";
  if (["insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"].includes(error.category ?? ""))
    return `${provider} credits or spending limits blocked processing. Check the API project's billing and limits, then retry.`;
  if (error.category === "gemini_daily_quota")
    return "Gemini's project has reached its daily quota. Check its active limits in Google AI Studio, then retry after the quota resets.";
  if (error.http_status === 429)
    return `${provider} returned HTTP 429. Check the API project's credits and rate limits, then retry when access is available.`;
  if (error.http_status === 401)
    return `${provider} rejected the API key. Check the selected provider's key in the backend terminal and restart the backend.`;
  if (error.http_status === 403)
    return `${provider} cannot access the configured model. Check the selected provider's model setting.`;
  return error.message;
}

export function Workspace({ documentId, onNew }: { documentId: string; onNew: () => void }) {
  const [document, setDocument] = useState<DocumentInfo | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [tab, setTab] = useState<"findings" | "claims">("findings");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [metadata, update] = await Promise.all([
          api<DocumentInfo>(`/documents/${documentId}`, { signal: controller.signal }),
          api<Progress>(`/documents/${documentId}/progress`, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        setDocument(metadata); setProgress(update); setError("");
        if (!isFinished(update.status)) timer = setTimeout(poll, 2000);
      } catch (error) {
        if (!controller.signal.aborted) { setError(errorMessage(error)); timer = setTimeout(poll, 5000); }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [documentId, attempt]);

  async function retryProcessing() {
    setRetrying(true); setError("");
    try {
      await api(`/documents/${documentId}/process`, { method: "POST" });
      setProgress(previous => previous ? { ...previous, status: "QUEUED", stage: "QUEUED", errors: [] } : null);
      setAttempt(value => value + 1);
    } catch (error) { setError(errorMessage(error)); }
    finally { setRetrying(false); }
  }
  async function cancelAndUploadAnother() {
    setCancelling(true); setError("");
    try {
      await api(`/documents/${documentId}/cancel`, { method: "POST" });
      onNew();
    } catch (error) { setError(errorMessage(error)); }
    finally { setCancelling(false); }
  }
  const status = progress?.status ?? "QUEUED";
  const failed = status === "FAILED" || (status === "COMPLETED_WITH_WARNINGS" && !!progress?.errors.length && progress.counts.claims === 0);
  const cancelled = status === "CANCELLED";
  const pending = status === "COMPLETED_WITH_PENDING_VERIFICATION";
  const warnings = status === "COMPLETED_WITH_WARNINGS";
  const complete = pending || status === "COMPLETED" || (warnings && !failed);
  const terminal = isFinished(status);
  const active = failed ? (stageIndex[progress?.errors.at(-1)?.stage ?? "PARSING"] ?? 0) : (stageIndex[progress?.stage ?? status] ?? stageIndex[status] ?? 0);
  return <>
    <div className="document-heading"><div><p className="eyebrow">DOCUMENT REVIEW</p><h1>{document?.filename ?? "Loading document..."}</h1></div>{terminal ? <button className="secondary" onClick={onNew}>Upload another PDF</button> : <button className="secondary" disabled={cancelling} onClick={cancelAndUploadAnother}>{cancelling ? "Cancelling..." : "Cancel and upload another PDF"}</button>}</div>
    {error && <Notice retry={() => setAttempt(value => value + 1)}>{error}</Notice>}
    <section className="progress-panel" aria-label="Processing progress">
      <div className="progress-title"><h2>{status === "CANCELLED" ? "Processing was cancelled" : failed ? "Processing needs attention" : warnings ? "Review ready; some work is incomplete" : complete ? "Your review is ready" : status === "QUEUED" ? "Your document is queued" : "Reviewing your document"}</h2><span className={`badge ${failed || warnings || status === "CANCELLED" ? "amber" : "green"}`} role="status">{status === "CANCELLED" ? "Cancelled" : failed ? "Processing failed" : warnings ? "Completed with warnings" : complete ? "Completed" : "In progress"}</span></div>
      <ol className="steps">{steps.map((step, index) => <li key={step} className={index < active || (complete && index === active) ? "done" : index === active ? failed ? "step-failed" : !terminal ? "current" : "" : ""} aria-current={!terminal && index === active ? "step" : undefined}><span className="step-dot" aria-hidden="true">{index < active || complete ? "✓" : index + 1}</span><span>{step}</span></li>)}</ol>
      {!terminal && <p className="muted progress-note">Updates automatically. Large documents may take a few minutes. You can return to this page while processing continues.</p>}
      {status === "CANCELLED" && <p className="muted progress-note">Processing stopped. Any claims and results completed before cancellation are still available.</p>}
      {pending && <div className="notice warning"><div><strong>Review ready; verification is pending.</strong><p>{progress?.counts.candidates_pending} comparisons remain. Resume when quota is available. Completed results are saved.</p></div><button className="secondary" disabled={retrying} onClick={retryProcessing}>{retrying ? "Resuming..." : "Resume verification"}</button></div>}
      {(failed || warnings) && <div className="notice warning"><div><strong>{failed ? "Processing could not check this document." : "These results are incomplete."}</strong><p>{progress?.counts.claims === 0 ? "No claims were extracted, so the document has not been checked. " : "Some claims or comparisons could not be completed. "}{failureAdvice(progress)}</p>{!!progress?.errors.length && <details><summary>Processing details ({progress.errors.length})</summary><ul>{progress.errors.slice(0, 10).map((item, index) => <li key={index}>{humanLabel(item.stage)}: {item.message}</li>)}</ul></details>}</div><button className="secondary" disabled={retrying} onClick={retryProcessing}>{retrying ? "Retrying..." : "Retry processing"}</button></div>}
    </section>
    {progress && <div className="stats"><div><span>Pages</span><strong>{progress.counts.pages.toLocaleString()}</strong></div><div><span>Extracted claims</span><strong>{progress.counts.claims.toLocaleString()}</strong></div><div><span>Possible contradictions</span><strong>{progress.counts.verified_contradictions.toLocaleString()}</strong></div></div>}
    {progress && <details className="progress-panel"><summary>Developer metrics</summary><dl>{[
      ["Chunks", progress.counts.chunks], ["Locally extracted facts", progress.counts.locally_extracted_facts],
      ["Candidates before filtering", progress.counts.candidates_before_filtering],
      ["Candidates after filtering", progress.counts.candidates_after_filtering],
      ["Gemini requests", progress.counts.gemini_requests], ["Candidates verified", progress.counts.candidates_verified],
      ["Candidates pending", progress.counts.candidates_pending], ["Candidates failed", progress.counts.candidates_failed],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? 0}</dd></div>)}</dl></details>}
    <div className="tabs" role="tablist" aria-label="Document results" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "findings" : event.key === "End" ? "claims" : tab === "findings" ? "claims" : "findings";
      setTab(next);
      event.currentTarget.querySelector<HTMLButtonElement>(`#${next}-tab`)?.focus();
    }}>
      <button id="findings-tab" role="tab" aria-selected={tab === "findings"} aria-controls="findings-panel" onClick={() => setTab("findings")}>Inconsistencies{progress ? <span>{progress.counts.verified_contradictions}</span> : null}</button>
      <button id="claims-tab" role="tab" aria-selected={tab === "claims"} aria-controls="claims-panel" onClick={() => setTab("claims")}>Claims{progress ? <span>{progress.counts.claims}</span> : null}</button>
    </div>
    <div role="tabpanel" id={tab === "findings" ? "findings-panel" : "claims-panel"} aria-labelledby={tab === "findings" ? "findings-tab" : "claims-tab"}>
      {tab === "claims" ? <Claims key={`${documentId}-${progress?.counts.claims ?? 0}`} documentId={documentId} /> :
        <Findings key={`${documentId}-${status}-${progress?.counts.verified_contradictions ?? 0}`} documentId={documentId}
          status={status} progress={progress} partial={failed || warnings || cancelled || pending} />}
    </div>
  </>;
}
