"""Логика учёта. Каждая публичная функция = одна транзакция.

Правила, на которых всё держится:
  1. Остаток меняется только вместе с записью в журнал movements (в одной транзакции).
  2. Строку остатка блокируем FOR UPDATE и проверяем «доступно >= нужно» под блокировкой,
     так что два параллельных резерва не уйдут в минус. Плюс CHECK в БД как страховка.
  3. Подтверждения (отправить/принять/отменить) - переходы конечного автомата по заблокированной
     строке документа. Повтор того же перехода возвращает результат и ничего не двигает.
  4. Создание документов идемпотентно по client_request_id (двойной клик / ретрай сети).
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    CANCELLED, DONE, FBO, FBO_MP, FBO_TRANSIT, FBS, FBS_TRANSIT, FF_AVAILABLE, FF_RESERVED,
    IN_TRANSIT, RECEIPT, RECEIVED, RESERVED, SHIPPED, SUPPLIER, TRANSFER, TRANSFER_TRANSIT,
    Document, FulfillmentCenter, Movement, Product, Stock,
)


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# ───────────────────────── вспомогательное ─────────────────────────

def _product(session: Session, sku: str) -> Product:
    p = session.get(Product, sku)
    if not p:
        raise DomainError(404, "PRODUCT_NOT_FOUND", f"Артикул {sku} не найден")
    return p


def _ff(session: Session, ff_id: int) -> FulfillmentCenter:
    ff = session.get(FulfillmentCenter, ff_id)
    if not ff:
        raise DomainError(404, "FF_NOT_FOUND", f"ФФ #{ff_id} не найден")
    return ff


def _require_active(ff: FulfillmentCenter, action: str) -> None:
    if ff.archived_at is not None:
        raise DomainError(
            409, "FF_ARCHIVED",
            f"ФФ «{ff.name}» удалён: {action} на нём нельзя. С него можно только вывезти остатки.",
        )


def _lock_stock(session: Session, sku: str, ff_id: int) -> Stock:
    """Строка остатка (создаём нулевую при первом обращении) под FOR UPDATE."""
    session.execute(
        pg_insert(Stock).values(sku=sku, ff_id=ff_id, on_hand=0, reserved=0).on_conflict_do_nothing()
    )
    return session.scalars(
        select(Stock).where(Stock.sku == sku, Stock.ff_id == ff_id).with_for_update()
    ).one()


def _require_available(session: Session, stock: Stock, qty: int) -> None:
    """Нехватка на выбранном ФФ. Если в целом товара хватает - подсказываем, где он лежит."""
    available = stock.on_hand - stock.reserved
    if available >= qty:
        return
    avail_expr = Stock.on_hand - Stock.reserved
    rows = session.execute(
        select(FulfillmentCenter.id, FulfillmentCenter.name, FulfillmentCenter.archived_at, avail_expr)
        .join(Stock, Stock.ff_id == FulfillmentCenter.id)
        .where(Stock.sku == stock.sku, Stock.ff_id != stock.ff_id, avail_expr > 0)
        .order_by(avail_expr.desc())
    ).all()
    alternatives = [
        {"ff_id": r[0], "name": r[1], "archived": r[2] is not None, "available": r[3]} for r in rows
    ]
    total = available + sum(a["available"] for a in alternatives)
    ff_name = session.get(FulfillmentCenter, stock.ff_id).name
    shortage = qty - available
    if total >= qty:
        code = "INSUFFICIENT_AT_FF"
        msg = (f"На ФФ «{ff_name}» доступно {available} из {qty}, не хватает {shortage}. "
               f"Всего по всем ФФ доступно {total}: можно перевезти недостающее.")
    else:
        code = "INSUFFICIENT_TOTAL"
        msg = f"Не хватает товара: нужно {qty}, доступно на всех ФФ вместе {total}."
    raise DomainError(
        409, code, msg, sku=stock.sku, ff_id=stock.ff_id, requested=qty, available=available,
        shortage=shortage, total_available=total, alternatives=alternatives,
    )


def _move(session: Session, doc: Document, op: str, qty: int, frm: tuple, to: tuple) -> None:
    session.add(Movement(
        document_id=doc.id, op=op, sku=doc.sku, qty=qty,
        from_bucket=frm[0], from_ff_id=frm[1], to_bucket=to[0], to_ff_id=to[1],
    ))


def _doc_dict(d: Document) -> dict:
    return {
        "id": d.id, "kind": d.kind, "status": d.status, "sku": d.sku, "qty": d.qty,
        "from_ff_id": d.from_ff_id, "to_ff_id": d.to_ff_id, "reference": d.reference,
        "created_at": d.created_at, "updated_at": d.updated_at,
    }


def _result(doc: Document, already_applied: bool) -> dict:
    return {"document": _doc_dict(doc), "already_applied": already_applied}


def _find_by_request_id(session: Session, crid: str | None) -> Document | None:
    if not crid:
        return None
    return session.scalars(select(Document).where(Document.client_request_id == crid)).first()


def _replay(doc: Document, kind: str) -> dict:
    if doc.kind != kind:
        raise DomainError(409, "REQUEST_ID_REUSED", "Этот ключ запроса уже использован для другой операции")
    return _result(doc, True)


def _create_document(session: Session, *, kind: str, status: str, sku: str, qty: int,
                     crid: str | None, from_ff: int | None = None, to_ff: int | None = None,
                     reference: str | None = None, work) -> dict:
    """Создать документ и выполнить work(doc) в одной транзакции; идемпотентно по crid."""
    existing = _find_by_request_id(session, crid)
    if existing:
        return _replay(existing, kind)
    doc = Document(kind=kind, status=status, sku=sku, qty=qty, from_ff_id=from_ff,
                   to_ff_id=to_ff, reference=reference, client_request_id=crid)
    session.add(doc)
    try:
        session.flush()
    except IntegrityError:  # параллельный запрос с тем же ключом успел раньше
        session.rollback()
        existing = _find_by_request_id(session, crid)
        if existing:
            return _replay(existing, kind)
        raise
    work(doc)
    session.commit()
    return _result(doc, False)


def _transition(session: Session, doc_id: int, kind: str, allowed_from: str, to_status: str, work) -> dict:
    """Подтверждение = переход статуса под блокировкой документа."""
    doc = session.scalars(select(Document).where(Document.id == doc_id).with_for_update()).first()
    if not doc or doc.kind != kind:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", f"Документ #{doc_id} не найден")
    if doc.status == to_status:  # повторное подтверждение: ничего не двигаем
        return _result(doc, True)
    if doc.status != allowed_from:
        raise DomainError(
            409, "INVALID_STATE",
            f"Документ #{doc.id} в статусе «{doc.status}», перейти в «{to_status}» нельзя",
        )
    work(doc)
    doc.status = to_status
    session.commit()
    return _result(doc, False)


# ───────────────────────── ФФ ─────────────────────────

def list_ffs(session: Session) -> list[dict]:
    rows = session.scalars(select(FulfillmentCenter).order_by(FulfillmentCenter.id)).all()
    return [{"id": f.id, "name": f.name, "archived": f.archived_at is not None} for f in rows]


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 100:
        raise DomainError(422, "VALIDATION", "Название ФФ: от 1 до 100 символов")
    return name


def _commit_ff(session: Session, ff: FulfillmentCenter) -> dict:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise DomainError(409, "DUPLICATE_NAME", f"ФФ с названием «{ff.name}» уже есть")
    return {"id": ff.id, "name": ff.name, "archived": ff.archived_at is not None}


def create_ff(session: Session, name: str) -> dict:
    ff = FulfillmentCenter(name=_clean_name(name))
    session.add(ff)
    return _commit_ff(session, ff)


def rename_ff(session: Session, ff_id: int, name: str) -> dict:
    ff = _ff(session, ff_id)
    _require_active(ff, "переименование")
    ff.name = _clean_name(name)
    return _commit_ff(session, ff)


def archive_ff(session: Session, ff_id: int) -> dict:
    """«Удаление» ФФ. Остатки и история остаются; ФФ перестаёт принимать товар."""
    ff = _ff(session, ff_id)
    if ff.archived_at is None:
        ff.archived_at = datetime.now(timezone.utc)
        session.commit()
    return {"id": ff.id, "name": ff.name, "archived": True}


def restore_ff(session: Session, ff_id: int) -> dict:
    ff = _ff(session, ff_id)
    ff.archived_at = None
    return _commit_ff(session, ff)


# ───────────────────────── приход ─────────────────────────

def receive_goods(session: Session, sku: str, ff_id: int, qty: int, crid: str | None) -> dict:
    _product(session, sku)
    _require_active(_ff(session, ff_id), "приход")

    def work(doc: Document) -> None:
        _lock_stock(session, sku, ff_id).on_hand += qty
        _move(session, doc, "RECEIPT", qty, (SUPPLIER, None), (FF_AVAILABLE, ff_id))

    return _create_document(session, kind=RECEIPT, status=DONE, sku=sku, qty=qty, crid=crid,
                            to_ff=ff_id, work=work)


# ───────────────────────── перемещение между ФФ ─────────────────────────

def create_transfer(session: Session, sku: str, from_ff: int, to_ff: int, qty: int, crid: str | None) -> dict:
    """Отправка: товар сразу уходит с ФФ-источника и числится «в пути» до приёмки."""
    if from_ff == to_ff:
        raise DomainError(422, "VALIDATION", "ФФ отправления и назначения совпадают")
    _product(session, sku)
    _ff(session, from_ff)  # источник может быть архивным: это способ вывезти остатки
    _require_active(_ff(session, to_ff), "приём перемещения")

    def work(doc: Document) -> None:
        st = _lock_stock(session, sku, from_ff)
        _require_available(session, st, qty)
        st.on_hand -= qty
        _move(session, doc, "TRANSFER_SHIP", qty, (FF_AVAILABLE, from_ff), (TRANSFER_TRANSIT, None))

    return _create_document(session, kind=TRANSFER, status=IN_TRANSIT, sku=sku, qty=qty, crid=crid,
                            from_ff=from_ff, to_ff=to_ff, work=work)


def receive_transfer(session: Session, doc_id: int) -> dict:
    def work(doc: Document) -> None:
        # Если ФФ-получателя уже «удалили», товар всё равно приехал и числится на нём.
        _lock_stock(session, doc.sku, doc.to_ff_id).on_hand += doc.qty
        _move(session, doc, "TRANSFER_RECEIVE", doc.qty, (TRANSFER_TRANSIT, None), (FF_AVAILABLE, doc.to_ff_id))

    return _transition(session, doc_id, TRANSFER, IN_TRANSIT, RECEIVED, work)


# ───────────────────────── FBO ─────────────────────────

def create_fbo(session: Session, sku: str, from_ff: int, qty: int, warehouse: str, crid: str | None) -> dict:
    """Отправка на склад маркетплейса: товар покидает ФФ и числится в пути до приёмки."""
    _product(session, sku)
    _ff(session, from_ff)

    def work(doc: Document) -> None:
        st = _lock_stock(session, sku, from_ff)
        _require_available(session, st, qty)
        st.on_hand -= qty
        _move(session, doc, "FBO_SHIP", qty, (FF_AVAILABLE, from_ff), (FBO_TRANSIT, None))

    return _create_document(session, kind=FBO, status=IN_TRANSIT, sku=sku, qty=qty, crid=crid,
                            from_ff=from_ff, reference=warehouse, work=work)


def accept_fbo(session: Session, doc_id: int) -> dict:
    def work(doc: Document) -> None:
        _move(session, doc, "FBO_ACCEPT", doc.qty, (FBO_TRANSIT, None), (FBO_MP, None))

    return _transition(session, doc_id, FBO, IN_TRANSIT, RECEIVED, work)


# ───────────────────────── FBS ─────────────────────────

def reserve_fbs(session: Session, sku: str, ff_id: int, qty: int, order_ref: str, crid: str | None) -> dict:
    """Резерв блокирует товар на ФФ (on_hand не меняется, available уменьшается)."""
    _product(session, sku)
    _require_active(_ff(session, ff_id), "резерв")

    def work(doc: Document) -> None:
        st = _lock_stock(session, sku, ff_id)
        _require_available(session, st, qty)
        st.reserved += qty
        _move(session, doc, "FBS_RESERVE", qty, (FF_AVAILABLE, ff_id), (FF_RESERVED, ff_id))

    return _create_document(session, kind=FBS, status=RESERVED, sku=sku, qty=qty, crid=crid,
                            from_ff=ff_id, reference=order_ref, work=work)


def ship_fbs(session: Session, doc_id: int) -> dict:
    def work(doc: Document) -> None:
        st = _lock_stock(session, doc.sku, doc.from_ff_id)
        st.reserved -= doc.qty
        st.on_hand -= doc.qty
        _move(session, doc, "FBS_SHIP", doc.qty, (FF_RESERVED, doc.from_ff_id), (FBS_TRANSIT, None))

    return _transition(session, doc_id, FBS, RESERVED, SHIPPED, work)


def cancel_fbs(session: Session, doc_id: int) -> dict:
    def work(doc: Document) -> None:
        _lock_stock(session, doc.sku, doc.from_ff_id).reserved -= doc.qty
        _move(session, doc, "FBS_CANCEL", doc.qty, (FF_RESERVED, doc.from_ff_id), (FF_AVAILABLE, doc.from_ff_id))

    return _transition(session, doc_id, FBS, RESERVED, CANCELLED, work)


# ───────────────────────── чтение ─────────────────────────

_TRANSIT_KEYS = {
    (TRANSFER, IN_TRANSIT): "between_ff",
    (FBO, IN_TRANSIT): "to_marketplace",
    (FBO, RECEIVED): "at_marketplace",
    (FBS, SHIPPED): "to_customer",
}


def stock_view(session: Session) -> dict:
    ffs = list_ffs(session)
    stock = defaultdict(dict)
    for s in session.scalars(select(Stock)):
        stock[s.sku][s.ff_id] = s
    transit = defaultdict(lambda: dict.fromkeys(_TRANSIT_KEYS.values(), 0))
    for sku, kind, status, qty in session.execute(
        select(Document.sku, Document.kind, Document.status, func.sum(Document.qty))
        .group_by(Document.sku, Document.kind, Document.status)
    ):
        key = _TRANSIT_KEYS.get((kind, status))
        if key:
            transit[sku][key] = int(qty)

    rows = []
    for p in session.scalars(select(Product).order_by(Product.sku)):
        per_ff, on_hand, reserved = {}, 0, 0
        for ff in ffs:
            s = stock[p.sku].get(ff["id"])
            oh, rs = (s.on_hand, s.reserved) if s else (0, 0)
            per_ff[ff["id"]] = {"on_hand": oh, "reserved": rs, "available": oh - rs}
            on_hand += oh
            reserved += rs
        t = dict(transit[p.sku])
        rows.append({
            "sku": p.sku, "name": p.name, "per_ff": per_ff,
            "totals": {"on_hand": on_hand, "reserved": reserved, "available": on_hand - reserved},
            "in_transit": t,
            "total_tracked": on_hand + sum(t.values()),
        })
    return {"ffs": ffs, "rows": rows}


def list_documents(session: Session, kind: str | None = None, active_only: bool = False) -> list[dict]:
    q = select(Document).order_by(Document.id.desc())
    if kind:
        q = q.where(Document.kind == kind)
    if active_only:
        q = q.where(Document.status.in_([IN_TRANSIT, RESERVED]))
    return [_doc_dict(d) for d in session.scalars(q.limit(300))]


def list_movements(session: Session, sku: str | None = None, ff_id: int | None = None,
                   limit: int = 200) -> list[dict]:
    q = select(Movement).order_by(Movement.id.desc()).limit(limit)
    if sku:
        q = q.where(Movement.sku == sku)
    if ff_id:
        q = q.where((Movement.from_ff_id == ff_id) | (Movement.to_ff_id == ff_id))
    return [
        {"id": m.id, "created_at": m.created_at, "document_id": m.document_id, "op": m.op, "sku": m.sku,
         "qty": m.qty, "from_bucket": m.from_bucket, "from_ff_id": m.from_ff_id,
         "to_bucket": m.to_bucket, "to_ff_id": m.to_ff_id}
        for m in session.scalars(q)
    ]


def audit(session: Session) -> dict:
    """Пересчитать всё по журналу и сверить с остатками и документами."""
    bal: dict[tuple, int] = defaultdict(int)
    for sku, bucket, ff, q in session.execute(
        select(Movement.sku, Movement.to_bucket, Movement.to_ff_id, func.sum(Movement.qty))
        .group_by(Movement.sku, Movement.to_bucket, Movement.to_ff_id)
    ):
        bal[(sku, bucket, ff)] += int(q)
    for sku, bucket, ff, q in session.execute(
        select(Movement.sku, Movement.from_bucket, Movement.from_ff_id, func.sum(Movement.qty))
        .group_by(Movement.sku, Movement.from_bucket, Movement.from_ff_id)
    ):
        bal[(sku, bucket, ff)] -= int(q)

    problems: list[str] = []
    for (sku, bucket, ff), v in bal.items():
        if bucket != SUPPLIER and v < 0:
            problems.append(f"{sku}: отрицательный баланс {bucket} (ФФ {ff}) = {v}")

    seen = set()
    for s in session.scalars(select(Stock)):
        seen.add((s.sku, s.ff_id))
        avail, res = bal[(s.sku, FF_AVAILABLE, s.ff_id)], bal[(s.sku, FF_RESERVED, s.ff_id)]
        if s.on_hand != avail + res or s.reserved != res:
            problems.append(
                f"{s.sku} на ФФ {s.ff_id}: остаток on_hand={s.on_hand}/reserved={s.reserved}, "
                f"по журналу {avail + res}/{res}"
            )
    for (sku, bucket, ff), v in bal.items():
        if bucket in (FF_AVAILABLE, FF_RESERVED) and v != 0 and (sku, ff) not in seen:
            problems.append(f"{sku}: по журналу есть товар на ФФ {ff}, но строки остатка нет")

    doc_sum: dict[tuple, int] = defaultdict(int)
    for sku, kind, status, qty in session.execute(
        select(Document.sku, Document.kind, Document.status, func.sum(Document.qty))
        .group_by(Document.sku, Document.kind, Document.status)
    ):
        doc_sum[(sku, kind, status)] = int(qty)
    checks = [
        (TRANSFER_TRANSIT, TRANSFER, IN_TRANSIT), (FBO_TRANSIT, FBO, IN_TRANSIT),
        (FBO_MP, FBO, RECEIVED), (FBS_TRANSIT, FBS, SHIPPED),
    ]
    for sku in session.scalars(select(Product.sku)):
        for bucket, kind, status in checks:
            if bal[(sku, bucket, None)] != doc_sum[(sku, kind, status)]:
                problems.append(
                    f"{sku}: {bucket} по журналу {bal[(sku, bucket, None)]}, "
                    f"по документам {doc_sum[(sku, kind, status)]}"
                )
    return {"ok": not problems, "problems": problems}
