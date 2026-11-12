import { useState } from "react";
import { api, ApiError, type AuditResult, type StockView } from "../api";
import type { Notify } from "../App";

export default function StockTab({ stock, notify }: { stock: StockView; notify: Notify }) {
  const [audit, setAudit] = useState<AuditResult | null>(null);

  // Удалённые ФФ показываем, пока на них числится товар: он не пропал.
  const visible = stock.ffs.filter(
    (f) => !f.archived || stock.rows.some((r) => r.per_ff[f.id]?.on_hand > 0),
  );

  const runAudit = async () => {
    try {
      const res = await api.get<AuditResult>("/audit");
      setAudit(res);
      notify(res.ok
        ? { kind: "ok", text: "Сверка с журналом: остатки сходятся" }
        : { kind: "error", text: `Расхождения: ${res.problems.join("; ")}` });
    } catch (e) {
      notify({ kind: "error", text: e instanceof ApiError ? e.message : "Ошибка сверки" });
    }
  };

  return (
    <section>
      <div className="toolbar">
        <h2>Остатки по артикулам и ФФ</h2>
        <button className="secondary" onClick={runAudit}>
          Сверить с журналом {audit && (audit.ok ? "✓" : "✗")}
        </button>
      </div>
      <div className="table-wrap">
        <table className="stock">
          <thead>
            <tr>
              <th rowSpan={2}>Артикул</th>
              {visible.map((f) => (
                <th key={f.id} colSpan={3} className={`group ${f.archived ? "archived" : ""}`}>
                  {f.name}{f.archived && " (удалён)"}
                </th>
              ))}
              <th colSpan={4} className="group transit">В пути и отправлено</th>
              <th rowSpan={2}>Всего учтено</th>
            </tr>
            <tr>
              {visible.map((f) => (
                <FfSubHeads key={f.id} />
              ))}
              <th className="transit" title="Отправлено с ФФ, ещё не принято на другом ФФ">Между ФФ</th>
              <th className="transit" title="FBO: покинуло ФФ, ещё не принято маркетплейсом">На склад МП</th>
              <th className="transit" title="FBO: принято на складе маркетплейса">Принято на МП</th>
              <th className="transit" title="FBS: отправлено клиенту">К клиенту</th>
            </tr>
          </thead>
          <tbody>
            {stock.rows.map((r) => (
              <tr key={r.sku}>
                <td className="sku"><b>{r.sku}</b><br /><span className="muted">{r.name}</span></td>
                {visible.map((f) => {
                  const c = r.per_ff[f.id];
                  return (
                    <FfCells key={f.id} onHand={c.on_hand} available={c.available} reserved={c.reserved} />
                  );
                })}
                <td className="num transit">{r.in_transit.between_ff || "·"}</td>
                <td className="num transit">{r.in_transit.to_marketplace || "·"}</td>
                <td className="num transit">{r.in_transit.at_marketplace || "·"}</td>
                <td className="num transit">{r.in_transit.to_customer || "·"}</td>
                <td className="num total">{r.total_tracked}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Физически - лежит на ФФ. Доступно = физически − резерв FBS. Резерв блокирует товар до отправки
        заказа или отмены. «Всего учтено» не меняется при перемещениях и отправках: товар только
        переходит из одной колонки в другую.
      </p>
    </section>
  );
}

function FfSubHeads() {
  return (
    <>
      <th title="Физически на складе ФФ">Физ.</th>
      <th title="Физически минус резерв FBS">Доступно</th>
      <th title="Зарезервировано под FBS">Резерв FBS</th>
    </>
  );
}

function FfCells({ onHand, available, reserved }: { onHand: number; available: number; reserved: number }) {
  return (
    <>
      <td className="num">{onHand}</td>
      <td className={`num avail ${available === 0 ? "zero" : ""}`}>{available}</td>
      <td className={`num ${reserved ? "reserved" : "zero"}`}>{reserved}</td>
    </>
  );
}
