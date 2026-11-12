import type { Doc, FF, Kind } from "./api";

export const KIND_LABEL: Record<Kind, string> = {
  RECEIPT: "Приход", TRANSFER: "Перемещение", FBO: "Отправка FBO", FBS: "Заказ FBS",
};

const STATUS_LABEL: Record<string, Partial<Record<Kind, string>> & { _?: string }> = {
  done: { RECEIPT: "Оприходовано" },
  in_transit: { TRANSFER: "В пути между ФФ", FBO: "В пути на склад МП" },
  received: { TRANSFER: "Принято на ФФ", FBO: "Принято на складе МП" },
  reserved: { FBS: "В резерве" },
  shipped: { FBS: "Едет к клиенту" },
  cancelled: { FBS: "Отменён" },
};
export const statusLabel = (d: Doc) => STATUS_LABEL[d.status]?.[d.kind] ?? d.status;

export const OP_LABEL: Record<string, string> = {
  RECEIPT: "Приход", TRANSFER_SHIP: "Отправка перемещения", TRANSFER_RECEIVE: "Приёмка перемещения",
  FBO_SHIP: "Отправка на МП (FBO)", FBO_ACCEPT: "Приёмка на МП (FBO)", FBS_RESERVE: "Резерв FBS",
  FBS_CANCEL: "Отмена резерва FBS", FBS_SHIP: "Отправка клиенту (FBS)",
};

export const ffName = (ffs: FF[], id: number | null) => {
  if (id == null) return "";
  const f = ffs.find((x) => x.id === id);
  return f ? (f.archived ? `${f.name} (удалён)` : f.name) : `ФФ #${id}`;
};

/** Человекочитаемое «место», где находится товар. */
export function bucketLabel(bucket: string, ffId: number | null, ffs: FF[]): string {
  switch (bucket) {
    case "SUPPLIER": return "Поставщик";
    case "FF_AVAILABLE": return `ФФ ${ffName(ffs, ffId)}`;
    case "FF_RESERVED": return `Резерв FBS · ${ffName(ffs, ffId)}`;
    case "TRANSFER_TRANSIT": return "В пути между ФФ";
    case "FBO_TRANSIT": return "В пути на склад МП";
    case "FBO_MP": return "Склад маркетплейса";
    case "FBS_TRANSIT": return "В пути к клиенту";
    default: return bucket;
  }
}

export function routeText(d: Doc, ffs: FF[]): string {
  const from = ffName(ffs, d.from_ff_id), to = ffName(ffs, d.to_ff_id);
  switch (d.kind) {
    case "RECEIPT": return `поставщик → ${to}`;
    case "TRANSFER": return `${from} → ${to}`;
    case "FBO": return `${from} → МП «${d.reference}»`;
    case "FBS": return `${from} · заказ ${d.reference}`;
  }
}

export const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
