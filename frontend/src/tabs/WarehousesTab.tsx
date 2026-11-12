import { useState } from "react";
import { api, ApiError, type FF, type StockView } from "../api";
import type { Notify } from "../App";

interface Props { stock: StockView; reload: () => Promise<void>; notify: Notify }

export default function WarehousesTab({ stock, reload, notify }: Props) {
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const total = (ffId: number) => stock.rows.reduce((s, r) => s + (r.per_ff[ffId]?.on_hand ?? 0), 0);

  const run = async (fn: () => Promise<unknown>, okText: string) => {
    try {
      await fn();
      notify({ kind: "ok", text: okText });
      setEditing(null); setConfirmId(null);
    } catch (e) {
      notify({ kind: "error", text: e instanceof ApiError ? e.message : "Ошибка" });
    }
    await reload();
  };

  const archived = stock.ffs.filter((f) => f.archived);

  return (
    <section>
      <div className="card">
        <h2>Склады фулфилмента</h2>
        <form className="inline" onSubmit={(e) => { e.preventDefault(); void run(() => api.post("/ffs", { name }), `ФФ «${name.trim()}» добавлен`).then(() => setName("")); }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название нового ФФ" />
          <button className="primary" disabled={!name.trim()}>Добавить</button>
        </form>
        <table>
          <thead><tr><th>Название</th><th>Статус</th><th className="num">Физически товара</th><th></th></tr></thead>
          <tbody>
            {stock.ffs.map((f) => (
              <tr key={f.id} className={f.archived ? "archived-row" : ""}>
                <td>
                  {editing?.id === f.id ? (
                    <form className="inline" onSubmit={(e) => { e.preventDefault(); void run(() => api.patch(`/ffs/${f.id}`, { name: editing.name }), "Переименовано"); }}>
                      <input autoFocus value={editing.name} onChange={(e) => setEditing({ id: f.id, name: e.target.value })} />
                      <button className="primary">Сохранить</button>
                      <button type="button" className="secondary" onClick={() => setEditing(null)}>Отмена</button>
                    </form>
                  ) : f.name}
                </td>
                <td>{f.archived ? <span className="badge cancelled">Удалён</span> : <span className="badge received">Активен</span>}</td>
                <td className="num">{total(f.id)}</td>
                <td className="actions">
                  {!f.archived && editing?.id !== f.id && confirmId !== f.id && (
                    <>
                      <button className="secondary" onClick={() => setEditing({ id: f.id, name: f.name })}>Переименовать</button>{" "}
                      <button className="danger" onClick={() => setConfirmId(f.id)}>Удалить</button>
                    </>
                  )}
                  {confirmId === f.id && (
                    <DeleteConfirm ff={f} units={total(f.id)}
                      onYes={() => run(() => api.del(`/ffs/${f.id}`), `ФФ «${f.name}» удалён, остатки сохранены`)}
                      onNo={() => setConfirmId(null)} />
                  )}
                  {f.archived && <button className="secondary" onClick={() => run(() => api.post(`/ffs/${f.id}/restore`), "ФФ восстановлен")}>Восстановить</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {archived.length > 0 && (
        <div className="card">
          <h2>Где числится товар удалённых ФФ</h2>
          <p className="muted small">
            Остатки и история сохранены. Принимать и резервировать на таких ФФ нельзя, но товар можно
            вывезти перемещением (вкладка «Операции»).
          </p>
          <table>
            <thead><tr><th>ФФ</th><th>Артикул</th><th>Товар</th><th className="num">Физически</th><th className="num">Резерв FBS</th></tr></thead>
            <tbody>
              {archived.flatMap((f) =>
                stock.rows.filter((r) => (r.per_ff[f.id]?.on_hand ?? 0) > 0).map((r) => (
                  <tr key={`${f.id}-${r.sku}`}>
                    <td>{f.name} (удалён)</td><td>{r.sku}</td><td>{r.name}</td>
                    <td className="num"><b>{r.per_ff[f.id].on_hand}</b></td>
                    <td className="num">{r.per_ff[f.id].reserved || "·"}</td>
                  </tr>
                )),
              )}
              {archived.every((f) => total(f.id) === 0) && <tr><td colSpan={5} className="muted">На удалённых ФФ товара нет</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function DeleteConfirm({ ff, units, onYes, onNo }: { ff: FF; units: number; onYes: () => void; onNo: () => void }) {
  return (
    <div className="box warn">
      {units > 0
        ? <>На «{ff.name}» числится {units} шт. Они сохранятся на удалённом ФФ и останутся в истории, их можно вывезти перемещением.</>
        : <>«{ff.name}» пуст. ФФ уйдёт в архив, история сохранится.</>}
      <div><button className="danger" onClick={onYes}>Да, удалить</button>{" "}<button className="secondary" onClick={onNo}>Отмена</button></div>
    </div>
  );
}
