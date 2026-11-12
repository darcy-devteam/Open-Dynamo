/** Calls to the Open-Dynamo server. Errors carry the server's explanation. */
export async function api<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/${path}`, body === undefined ? undefined : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error('Cannot reach the Open-Dynamo server.');
  }
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    const detail = info?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? (Array.isArray(detail) ? detail[0]?.msg : null) ?? `The server answered ${res.status}.`);
  }
  return res.json();
}

/** Format a number for display: four significant figures, no trailing noise. */
export function fmt(x: number | null | undefined, digits = 4): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  if (!Number.isFinite(x)) return x > 0 ? '∞' : '−∞';
  const a = Math.abs(x);
  if (a !== 0 && (a >= 1e7 || a < 1e-4)) return x.toExponential(Math.max(0, digits - 2)).replace('e+', 'e').replace('-', '−');
  return new Intl.NumberFormat('en', { maximumSignificantDigits: digits }).format(x).replace('-', '−');
}
