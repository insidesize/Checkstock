import { useEffect, useState } from "react";
import { api, type Movement, type StockView } from "../api";
import { bucketLabel, fmtTime, OP_LABEL } from "../labels";

export default function HistoryTab({ stock, version }: { stock: StockView; version: number }) {
  const [sku, setSku] = useState("");
  const [ffId, setFfId] = useState("");
  const [rows, setRows] = useState<Movement[]>([]);

  useEffect(() => {
    const qs = new URLSearchParams();
    if (sku) qs.set("sku", sku);
    if (ffId) qs.set("ff_id", ffId);
    void api.get<Movement[]>(`/movements?${qs}`).then(setRows).catch(() => setRows([]));
  }, [sku, ffId, version]);

  return (
    <section>
      <div className="toolbar">
        <h2>История операций</h2>
        <div className="filters">
          <select value={sku} onChange={(e) => setSku(e.target.value)}>
            <option value="">Все артикулы</option>
            {stock.rows.map((r) => <option key={r.sku} value={r.sku}>{r.sku} · {r.name}</option>)}
          </select>
          <select value={ffId} onChange={(e) => setFfId(e.target.value)}>
            <option value="">Все ФФ</option>
            {stock.ffs.map((f) => <option key={f.id} value={f.id}>{f.name}{f.archived ? " (удалён)" : ""}</option>)}
          </select>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Время</th><th>Операция</th><th className="num">Документ</th><th>Товар</th><th className="num">Кол-во</th><th>Откуда</th><th>Куда</th></tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id}>
                <td className="muted nowrap">{fmtTime(m.created_at)}</td>
                <td>{OP_LABEL[m.op] ?? m.op}</td>
                <td className="num">#{m.document_id}</td>
                <td>{m.sku}</td>
                <td className="num"><b>{m.qty}</b></td>
                <td>{bucketLabel(m.from_bucket, m.from_ff_id, stock.ffs)}</td>
                <td>{bucketLabel(m.to_bucket, m.to_ff_id, stock.ffs)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">Операций нет</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
