export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type DocumentSummary = {
  id: string;
  filename: string;
  file_type: string | null;
  file_size: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  units_total: number;
  units_done: number;
  progress_pct: number;
  created_at: string | null;
  finished_at: string | null;
};

export type UploadResult = {
  id?: string;
  filename: string;
  accepted: boolean;
  status?: string;
  error?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the default message */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const listDocuments = (status?: string) =>
  request<{ total: number; items: DocumentSummary[] }>(`/documents?limit=100${status ? `&status=${status}` : ""}`);

export function uploadFiles(files: File[]) {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  return request<{ files: UploadResult[] }>("/documents", { method: "POST", body: form });
}
