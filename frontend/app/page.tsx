"use client";

import Link from "next/link";
import { DragEvent, useCallback, useEffect, useRef, useState } from "react";

import StatusBadge from "@/components/StatusBadge";
import { DocumentSummary, listDocuments, UploadResult, uploadFiles } from "@/lib/api";
import { formatBytes } from "@/lib/format";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv";
const STATUSES = ["", "queued", "processing", "done", "needs_review", "failed"];

export default function HomePage() {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listDocuments(filter || undefined);
      setDocs(data.items);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the API.");
    }
  }, [filter]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [refresh]);

  async function send(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    setResults([]);
    try {
      const all: UploadResult[] = [];
      for (let i = 0; i < files.length; i += 50) {
        const res = await uploadFiles(files.slice(i, i + 50));
        all.push(...res.files);
      }
      setResults(all);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    send(Array.from(e.dataTransfer.files));
  }

  const rejected = results.filter((r) => !r.accepted);

  return (
    <>
      <h1>Documents</h1>

      <div
        className={`card drop ${dragging ? "active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p style={{ marginTop: 0 }}>Drag files here, or choose them from your computer.</p>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          PDF, JPG, PNG, TIFF, XLSX, XLS, CSV. Up to 200 MB per file.
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          disabled={uploading}
          onChange={(e) => send(Array.from(e.target.files ?? []))}
        />
        {uploading && <p className="muted">Uploading...</p>}
        {results.length > 0 && !uploading && (
          <p className="ok">
            {results.length - rejected.length} of {results.length} file(s) accepted.
          </p>
        )}
        {rejected.map((r, i) => (
          <p key={i} className="err" style={{ margin: ".2rem 0" }}>
            {r.filename}: {r.error}
          </p>
        ))}
      </div>

      {error && <p className="err">{error}</p>}

      <div className="card">
        <div className="row" style={{ marginBottom: ".75rem", justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>All documents ({total})</h2>
          <label className="row">
            <span className="muted">Status</span>
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s === "" ? "all" : s.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
        </div>

        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              <th>Size</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Uploaded</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>
                  <Link href={`/documents/${d.id}`}>{d.filename}</Link>
                  {d.error_message && <div className="err" style={{ fontSize: ".8rem" }}>{d.error_message}</div>}
                </td>
                <td>{d.file_type ?? "-"}</td>
                <td>{formatBytes(d.file_size)}</td>
                <td><StatusBadge status={d.status} /></td>
                <td>
                  <div className="bar"><div style={{ width: `${d.progress_pct}%` }} /></div>
                  <span className="muted" style={{ fontSize: ".75rem" }}>{d.units_done}/{d.units_total}</span>
                </td>
                <td className="muted">{d.created_at ? new Date(d.created_at).toLocaleString() : "-"}</td>
              </tr>
            ))}
            {docs.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">No documents yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
