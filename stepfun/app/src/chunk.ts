// Deterministic text chunking for document import.
export interface Chunk {
  text: string;
  idx: number;
}

const MAX_CHARS = 500;
const OVERLAP = 50;

export function chunkText(text: string): Chunk[] {
  const clean = text.replace(/\r\n/g, "\n").trim();
  if (!clean) return [];
  const paragraphs = clean
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean);

  const chunks: Chunk[] = [];
  let current = "";
  for (const para of paragraphs) {
    if (para.length > MAX_CHARS) {
      if (current) {
        chunks.push({ text: current, idx: chunks.length });
        current = "";
      }
      for (let i = 0; i < para.length; i += MAX_CHARS - OVERLAP) {
        chunks.push({ text: para.slice(i, i + MAX_CHARS), idx: chunks.length });
      }
      continue;
    }
    if (current && current.length + para.length + 1 > MAX_CHARS) {
      chunks.push({ text: current, idx: chunks.length });
      current = para;
    } else {
      current = current ? `${current}\n${para}` : para;
    }
  }
  if (current) chunks.push({ text: current, idx: chunks.length });
  return chunks;
}

export function docId(fileName: string, chunkIdx: number): string {
  const base = fileName.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]/g, "_").slice(0, 80);
  return `${base}:${chunkIdx}`;
}
