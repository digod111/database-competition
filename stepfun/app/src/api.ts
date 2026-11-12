// Thin HTTP client for the forwarding server (seekdb access lives there).

export interface SearchRow {
  id: string;
  doc_name: string;
  chunk_idx: number;
  content: string;
  dist: number;
}

export interface AddItem {
  id: string;
  doc_name: string;
  chunk_idx: number;
  content: string;
  embedding: number[];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST ${path} -> ${res.status}: ${text}`);
  }
  return res.json();
}

export const kbAdd = (kb: string, items: AddItem[], docCount: number) =>
  post<{ ok: boolean; inserted: number; elapsedMs: number }>("/api/kb/add", { kb, items, docCount });

export const kbSearch = (kb: string, queryText: string, vector: number[], k: number, mode: "hybrid" | "vector") =>
  post<{ ok: boolean; mode: string; rows: SearchRow[]; elapsedMs: number }>("/api/kb/search", {
    kb,
    queryText,
    vector: JSON.stringify(vector),
    k,
    mode,
  });

export const kbReset = (kb: string) => post<{ ok: boolean }>("/api/kb/reset", { kb });

export const health = () => fetch("/api/health").then((r) => r.json());

export const loadJson = <T>(url: string) => fetch(url).then((r) => r.json()) as Promise<T>;

export const loadText = (url: string) => fetch(url).then((r) => r.text());
