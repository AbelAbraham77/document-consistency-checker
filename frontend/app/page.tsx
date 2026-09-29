"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, uuidPattern } from "../lib/api";
import { Notice } from "../components/review";
import { Workspace } from "../components/workspace";

function DocumentIcon() {
  return <svg width="28" height="32" viewBox="0 0 28 32" fill="none" aria-hidden="true"><path d="M5 1h12l7 7v21a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3a2 2 0 0 1 2-2Z" stroke="currentColor" strokeWidth="1.6"/><path d="M17 1v8h7M8 15h11M8 20h11M8 25h7" stroke="currentColor" strokeWidth="1.6"/></svg>;
}

export default function Home() {
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [error, setError] = useState("");
  const [recoverId, setRecoverId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const uploadLock = useRef(false);
  useEffect(() => {
    const restore = () => { const id = new URLSearchParams(window.location.search).get("document"); setDocumentId(id && uuidPattern.test(id) ? id : null); };
    restore(); window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  function openDocument(id: string | null) {
    setDocumentId(id); setError(""); setRecoverId(null);
    window.history.replaceState(null, "", id ? `/?document=${id}` : "/");
  }
  async function upload(file?: File) {
    if (!file || uploadLock.current) return;
    setError(""); setRecoverId(null);
    if (!file.name.toLowerCase().endsWith(".pdf")) { setError("Please choose a PDF document."); return; }
    if (file.size === 0) { setError("This file is empty. Please choose a PDF with content."); return; }
    if (file.size > 50 * 1024 * 1024) { setError("Please choose a PDF smaller than 50 MB."); return; }
    uploadLock.current = true; setUploading(true); setUploadName(file.name);
    const data = new FormData(); data.append("file", file);
    try {
      const result = await api<{ document_id: string }>("/documents", { method: "POST", body: data });
      openDocument(result.document_id);
    } catch (error) {
      setError(errorMessage(error));
      if (error instanceof ApiError && error.documentId) setRecoverId(error.documentId);
    } finally { setUploading(false); uploadLock.current = false; if (input.current) input.current.value = ""; }
  }
  return <div className="shell"><header className="site-header"><a href="/" className="brand"><span className="brand-mark" aria-hidden="true">⌁</span>crosscheck<span className="brand-dot">.</span></a><span className="header-caption">DOCUMENT CONSISTENCY</span></header>
    <main>{documentId ? <Workspace key={documentId} documentId={documentId} onNew={() => openDocument(null)} /> :
      <div className="upload-screen"><div className="intro"><p className="eyebrow">A CLEARER VIEW OF YOUR DOCUMENT</p><h1>Find the details<br />that don’t add up.</h1><p>Check factual statements across your PDF.<br className="desktop-break" /> Review potential inconsistencies with the source right beside them.</p></div>
        <div className={`upload-panel ${dragging ? "dragging" : ""}`} onDragOver={event => { event.preventDefault(); if (!uploading) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length > 1) setError("Please upload one PDF at a time."); else void upload(event.dataTransfer.files[0]); }}>
          <div className="file-icon"><DocumentIcon /></div><h2>{uploading ? "Uploading your document…" : "Start with a PDF"}</h2><p>{uploading ? uploadName : "Drag and drop your document here, or choose a file."}</p>
          <input ref={input} type="file" accept="application/pdf,.pdf" aria-label="Choose PDF" className="file-input" disabled={uploading} onChange={event => void upload(event.target.files?.[0])} />
          <button className="primary" disabled={uploading} onClick={() => input.current?.click()}>{uploading ? <><span className="spinner small" />Uploading…</> : <>Choose PDF <span aria-hidden="true">↗</span></>}</button><span className="file-hint">PDF documents · Up to 50 MB</span>
        </div>
        {error && <Notice>{error}</Notice>}{recoverId && <button className="secondary" onClick={() => openDocument(recoverId)}>Open saved document to retry processing</button>}
        <div className="how-it-works"><div><span>01</span><h3>Upload your document</h3><p>Annual reports, filings, or other factual documents.</p></div><div><span>02</span><h3>Let the review run</h3><p>Statements are compared with their dates, scope, and units.</p></div><div><span>03</span><h3>See the evidence</h3><p>Compare the original wording and surrounding context.</p></div></div>
      </div>}
    </main><footer>Crosscheck <span>Clarity, with context.</span></footer></div>;
}
