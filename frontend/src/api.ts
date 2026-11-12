export interface FF { id: number; name: string; archived: boolean }
export interface Cell { on_hand: number; reserved: number; available: number }
export interface InTransit { between_ff: number; to_marketplace: number; at_marketplace: number; to_customer: number }
export interface StockRow {
  sku: string; name: string; per_ff: Record<string, Cell>; totals: Cell;
  in_transit: InTransit; total_tracked: number;
}
export interface StockView { ffs: FF[]; rows: StockRow[] }

export type Kind = "RECEIPT" | "TRANSFER" | "FBO" | "FBS";
export interface Doc {
  id: number; kind: Kind; status: string; sku: string; qty: number;
  from_ff_id: number | null; to_ff_id: number | null; reference: string | null;
  created_at: string; updated_at: string;
}
export interface Movement {
  id: number; created_at: string; document_id: number; op: string; sku: string; qty: number;
  from_bucket: string; from_ff_id: number | null; to_bucket: string; to_ff_id: number | null;
}
export interface Alternative { ff_id: number; name: string; archived: boolean; available: number }
export interface ShortageDetail {
  code: "INSUFFICIENT_AT_FF" | "INSUFFICIENT_TOTAL"; message: string; sku: string; ff_id: number;
  requested: number; available: number; shortage: number; total_available: number;
  alternatives: Alternative[];
}
export interface ActionResult { document: Doc; already_applied: boolean }
export interface AuditResult { ok: boolean; problems: string[] }

export class ApiError extends Error {
  constructor(public status: number, public detail: { code: string; message: string } & Record<string, unknown>) {
    super(detail.message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, { code: "NETWORK", message: "Нет связи с сервером" });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, data?.detail ?? { code: "HTTP", message: res.statusText });
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/** Ключ идемпотентности для формы: повтор запроса с тем же ключом не создаст дубль. */
export const newRequestId = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
