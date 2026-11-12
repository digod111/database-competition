// Timing utilities: per-stage measurement, median aggregation, JSON export.

export interface StageTiming {
  stage: string;
  ms: number;
}

export class RunRecord {
  stages: StageTiming[] = [];
  extras: Record<string, number | string> = {};

  add(stage: string, ms: number) {
    this.stages.push({ stage, ms: Math.round(ms * 100) / 100 });
  }
}

export function median(xs: number[]): number {
  if (xs.length === 0) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN;
}

export function downloadJson(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// Benchmark runner: repeat fn n times, return all samples (caller decides median/first vs warm).
export async function repeat<T>(n: number, fn: (i: number) => Promise<T>): Promise<T[]> {
  const out: T[] = [];
  for (let i = 0; i < n; i++) out.push(await fn(i));
  return out;
}
