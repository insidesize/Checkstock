import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Doc, type StockView } from "./api";
import HistoryTab from "./tabs/HistoryTab";
import OperationsTab from "./tabs/OperationsTab";
import StockTab from "./tabs/StockTab";
import WarehousesTab from "./tabs/WarehousesTab";

export interface Notice { kind: "ok" | "info" | "error"; text: string }
export type Notify = (n: Notice) => void;

const TABS = [
  { id: "stock", label: "Остатки" },
  { id: "ops", label: "Операции" },
  { id: "ffs", label: "Склады ФФ" },
  { id: "history", label: "История" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("stock");
  const [stock, setStock] = useState<StockView | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [version, setVersion] = useState(0); // растёт после каждого изменения, чтобы обновлять историю
  const [notice, setNotice] = useState<Notice | null>(null);

  const reload = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([api.get<StockView>("/stock"), api.get<Doc[]>("/documents")]);
      setStock(s);
      setDocs(d);
      setVersion((v) => v + 1);
    } catch (e) {
      setNotice({ kind: "error", text: e instanceof ApiError ? e.message : "Не удалось загрузить данные" });
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice]);

  return (
    <div className="app">
      <header>
        <h1>CheckStock <span>где сейчас товар</span></h1>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      {notice && <div className={`notice ${notice.kind}`} onClick={() => setNotice(null)}>{notice.text}</div>}
      {!stock ? (
        <p className="muted">Загрузка…</p>
      ) : (
        <main>
          {tab === "stock" && <StockTab stock={stock} notify={setNotice} />}
          {tab === "ops" && <OperationsTab stock={stock} docs={docs} reload={reload} notify={setNotice} />}
          {tab === "ffs" && <WarehousesTab stock={stock} reload={reload} notify={setNotice} />}
          {tab === "history" && <HistoryTab stock={stock} version={version} />}
        </main>
      )}
    </div>
  );
}
