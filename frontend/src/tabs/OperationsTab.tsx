import { useRef, useState } from "react";
import {
  api, ApiError, newRequestId, type ActionResult, type Alternative, type Doc, type ShortageDetail,
  type StockView,
} from "../api";
import type { Notify } from "../App";
import { ffName, fmtTime, KIND_LABEL, routeText, statusLabel } from "../labels";

type Mode = "receipt" | "transfer" | "fbs" | "fbo";
const MODES: { id: Mode; label: string }[] = [
  { id: "receipt", label: "Приход" },
  { id: "fbs", label: "Резерв FBS" },
  { id: "transfer", label: "Перемещение между ФФ" },
  { id: "fbo", label: "Отправка FBO" },
];

interface Props { stock: StockView; docs: Doc[]; reload: () => Promise<void>; notify: Notify }

export default function OperationsTab({ stock, docs, reload, notify }: Props) {
  const active = stock.ffs.filter((f) => !f.archived);
  const [mode, setMode] = useState<Mode>("fbs");
  const [sku, setSku] = useState(stock.rows[0]?.sku ?? "");
  const [ffA, setFfA] = useState<number>(active[0]?.id ?? 0); // откуда / на какой ФФ
  const [ffB, setFfB] = useState<number>(active[1]?.id ?? active[0]?.id ?? 0); // куда (перемещение)
  const [qty, setQty] = useState("1");
  const [text, setText] = useState(""); // номер заказа FBS или склад МП для FBO
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shortage, setShortage] = useState<ShortageDetail | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [onlyActive, setOnlyActive] = useState(true);
  // Один ключ на «намерение»: если ответ потерялся и пользователь жмёт ещё раз, дубля не будет.
  const requestId = useRef(newRequestId());

  // Откуда можно брать товар: любой ФФ, в том числе удалённый (так его вывозят). Куда класть - только активные.
  const sources = mode === "transfer" || mode === "fbo" ? stock.ffs.filter((f) => !f.archived || hasStock(stock, f.id, sku)) : active;
  // Выбранный id мог устареть (ФФ удалили, сменили режим): в запрос уходит только то, что реально показано в списке.
  const fromId = sources.some((f) => f.id === ffA) ? ffA : sources[0]?.id ?? 0;
  const toId = active.some((f) => f.id === ffB) ? ffB : active[0]?.id ?? 0;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null); setShortage(null); setHint(null);
    const n = Number(qty);
    if (!Number.isInteger(n) || n <= 0) { setError("Количество - целое число больше нуля"); return; }
    const client_request_id = requestId.current;
    const calls: Record<Mode, [string, object]> = {
      receipt: ["/receipts", { sku, ff_id: fromId, qty: n, client_request_id }],
      fbs: ["/fbs", { sku, ff_id: fromId, qty: n, order_ref: text.trim(), client_request_id }],
      transfer: ["/transfers", { sku, from_ff_id: fromId, to_ff_id: toId, qty: n, client_request_id }],
      fbo: ["/fbo", { sku, from_ff_id: fromId, qty: n, marketplace_warehouse: text.trim(), client_request_id }],
    };
    if ((mode === "fbs" || mode === "fbo") && !text.trim()) {
      setError(mode === "fbs" ? "Укажите номер заказа" : "Укажите склад маркетплейса");
      return;
    }
    setBusy(true);
    try {
      const [path, body] = calls[mode];
      const res = await api.post<ActionResult>(path, body);
      notify(res.already_applied
        ? { kind: "info", text: `Операция #${res.document.id} уже была выполнена, повторно ничего не изменено` }
        : { kind: "ok", text: `Готово: ${KIND_LABEL[res.document.kind]} #${res.document.id}` });
      requestId.current = newRequestId();
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.detail.code.startsWith("INSUFFICIENT")) {
        setShortage(err.detail as unknown as ShortageDetail);
      } else {
        setError(err instanceof ApiError ? err.message : "Неизвестная ошибка");
      }
    } finally {
      setBusy(false);
    }
  };

  /** Подсказка при нехватке: превращаем форму в перемещение с нужного ФФ на выбранный. */
  const useAlternative = (s: ShortageDetail, alt: Alternative) => {
    setMode("transfer");
    setSku(s.sku);
    setFfA(alt.ff_id);
    setFfB(s.ff_id);
    setQty(String(Math.min(alt.available, s.shortage)));
    setShortage(null);
    setHint("Подставлено перемещение. Отправьте его, примите на ФФ во вкладке ниже и повторите исходную операцию.");
    requestId.current = newRequestId();
  };

  const act = async (path: string) => {
    try {
      const res = await api.post<ActionResult>(path);
      notify(res.already_applied
        ? { kind: "info", text: `#${res.document.id}: уже подтверждено ранее, товар повторно не списан` }
        : { kind: "ok", text: `#${res.document.id}: ${statusLabel(res.document)}` });
    } catch (err) {
      notify({ kind: "error", text: err instanceof ApiError ? err.message : "Ошибка операции" });
    }
    await reload();
  };

  const shown = docs.filter((d) => d.kind !== "RECEIPT" && (!onlyActive || d.status === "in_transit" || d.status === "reserved"));
  const skuName = (s: string) => stock.rows.find((r) => r.sku === s)?.name ?? "";

  return (
    <section className="ops">
      <form className="card" onSubmit={submit}>
        <h2>Новая операция</h2>
        <div className="modes">
          {MODES.map((m) => (
            <button type="button" key={m.id} className={mode === m.id ? "active" : ""}
              onClick={() => { setMode(m.id); setError(null); setShortage(null); setHint(null); }}>
              {m.label}
            </button>
          ))}
        </div>
        <label>Товар
          <select value={sku} onChange={(e) => setSku(e.target.value)}>
            {stock.rows.map((r) => <option key={r.sku} value={r.sku}>{r.sku} · {r.name}</option>)}
          </select>
        </label>
        <label>{mode === "receipt" ? "На какой ФФ" : mode === "fbs" ? "Резерв на ФФ" : "С какого ФФ"}
          <select value={fromId} onChange={(e) => setFfA(Number(e.target.value))}>
            {sources.map((f) => <option key={f.id} value={f.id}>{ffName(stock.ffs, f.id)}{showAvail(stock, f.id, sku, mode)}</option>)}
          </select>
        </label>
        {mode === "transfer" && (
          <label>На какой ФФ
            <select value={toId} onChange={(e) => setFfB(Number(e.target.value))}>
              {active.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
        )}
        {mode === "fbs" && <label>Номер заказа<input value={text} onChange={(e) => setText(e.target.value)} placeholder="например, WB-100500" /></label>}
        {mode === "fbo" && <label>Склад маркетплейса<input value={text} onChange={(e) => setText(e.target.value)} placeholder="например, Ozon Хоругвино" /></label>}
        <label>Количество
          <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <button className="primary" disabled={busy}>{busy ? "Выполняю…" : "Выполнить"}</button>

        {hint && <div className="box info">{hint}</div>}
        {error && <div className="box error">{error}</div>}
        {shortage && (
          <div className="box warn">
            <b>{shortage.code === "INSUFFICIENT_AT_FF" ? "Не хватает на выбранном ФФ" : "Не хватает товара"}</b>
            <p>{shortage.message}</p>
            {shortage.alternatives.map((alt) => (
              <button type="button" key={alt.ff_id} className="secondary" onClick={() => useAlternative(shortage, alt)}>
                Перевезти {Math.min(alt.available, shortage.shortage)} шт с «{alt.name}»{alt.archived && " (удалён)"} · там доступно {alt.available}
              </button>
            ))}
          </div>
        )}
      </form>

      <div className="card wide">
        <div className="toolbar">
          <h2>Отправки и перемещения</h2>
          <label className="check"><input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} /> только активные</label>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>№</th><th>Тип</th><th>Товар</th><th className="num">Кол-во</th><th>Маршрут</th><th>Статус</th><th>Обновлено</th><th></th></tr></thead>
            <tbody>
              {shown.map((d) => (
                <tr key={d.id}>
                  <td className="num">#{d.id}</td>
                  <td>{KIND_LABEL[d.kind]}</td>
                  <td>{d.sku}<br /><span className="muted small">{skuName(d.sku)}</span></td>
                  <td className="num"><b>{d.qty}</b></td>
                  <td>{routeText(d, stock.ffs)}</td>
                  <td><span className={`badge ${d.status}`}>{statusLabel(d)}</span></td>
                  <td className="muted nowrap small">{fmtTime(d.updated_at)}</td>
                  <td className="actions"><Actions d={d} act={act} /></td>
                </tr>
              ))}
              {shown.length === 0 && <tr><td colSpan={8} className="muted">Нет документов</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function Actions({ d, act }: { d: Doc; act: (path: string) => Promise<void> }) {
  const [pending, setPending] = useState(false);
  const run = async (path: string) => {
    if (pending) return;
    setPending(true);
    await act(path);
    setPending(false);
  };
  if (d.kind === "TRANSFER" && d.status === "in_transit")
    return <button disabled={pending} onClick={() => run(`/transfers/${d.id}/receive`)}>Принять на ФФ</button>;
  if (d.kind === "FBO" && d.status === "in_transit")
    return <button disabled={pending} onClick={() => run(`/fbo/${d.id}/accept`)}>Принято на МП</button>;
  if (d.kind === "FBS" && d.status === "reserved")
    return (
      <>
        <button disabled={pending} onClick={() => run(`/fbs/${d.id}/ship`)}>Отправить</button>{" "}
        <button className="secondary" disabled={pending} onClick={() => run(`/fbs/${d.id}/cancel`)}>Отменить</button>
      </>
    );
  return null;
}

function hasStock(stock: StockView, ffId: number, sku: string) {
  return (stock.rows.find((r) => r.sku === sku)?.per_ff[ffId]?.on_hand ?? 0) > 0;
}

function showAvail(stock: StockView, ffId: number, sku: string, mode: Mode) {
  if (mode === "receipt") return "";
  const c = stock.rows.find((r) => r.sku === sku)?.per_ff[ffId];
  return c ? ` · доступно ${c.available}` : "";
}
