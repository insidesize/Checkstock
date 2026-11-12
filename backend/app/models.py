"""Схема данных.

Три слоя правды:
  stock      - текущие остатки на ФФ (быстро читать, быстро блокировать);
  documents  - бизнес-операции с жизненным циклом (перемещение, FBO, FBS, приход);
  movements  - неизменяемый журнал: каждая единица товара всегда переходит из одного
               «места» (bucket) в другое. По нему /api/audit перепроверяет остатки.
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Места, где может находиться товар.
SUPPLIER = "SUPPLIER"  # вне системы (поставщик), источник прихода
FF_AVAILABLE = "FF_AVAILABLE"  # на ФФ, свободно
FF_RESERVED = "FF_RESERVED"  # на ФФ, зарезервировано под FBS
TRANSFER_TRANSIT = "TRANSFER_TRANSIT"  # в пути между ФФ
FBO_TRANSIT = "FBO_TRANSIT"  # в пути на склад маркетплейса
FBO_MP = "FBO_MP"  # принят на складе маркетплейса
FBS_TRANSIT = "FBS_TRANSIT"  # отправлен клиенту

# Типы документов и статусы.
RECEIPT, TRANSFER, FBO, FBS = "RECEIPT", "TRANSFER", "FBO", "FBS"
DONE, IN_TRANSIT, RECEIVED, RESERVED, SHIPPED, CANCELLED = (
    "done", "in_transit", "received", "reserved", "shipped", "cancelled",
)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class FulfillmentCenter(Base):
    __tablename__ = "fulfillment_centers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # «Удаление» = архивация: строка и остатки остаются, история не ломается.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Среди активных ФФ имена уникальны (без учёта регистра); у архивных можно повторять.
Index(
    "uq_ff_active_name",
    func.lower(FulfillmentCenter.name),
    unique=True,
    postgresql_where=FulfillmentCenter.archived_at.is_(None),
)


class Stock(Base):
    __tablename__ = "stock"
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    ff_id: Mapped[int] = mapped_column(ForeignKey("fulfillment_centers.id"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # физически
    reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # резерв FBS
    # available = on_hand - reserved (не хранится, чтобы не разъезжалось)

    # Последний рубеж: даже баг в коде не сможет увести остаток в минус.
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_on_hand_nonneg"),
        CheckConstraint("reserved >= 0", name="ck_reserved_nonneg"),
        CheckConstraint("reserved <= on_hand", name="ck_reserved_le_on_hand"),
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    qty: Mapped[int] = mapped_column(Integer)
    from_ff_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_centers.id"))
    to_ff_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_centers.id"))
    # FBO: склад маркетплейса; FBS: номер заказа
    reference: Mapped[str | None] = mapped_column(String(200))
    # Ключ идемпотентности создания: повторный POST с тем же ключом не создаёт дубль.
    client_request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (CheckConstraint("qty > 0", name="ck_doc_qty_positive"),)


class Movement(Base):
    __tablename__ = "movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    op: Mapped[str] = mapped_column(String(24))
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    from_bucket: Mapped[str] = mapped_column(String(24))
    from_ff_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_centers.id"))
    to_bucket: Mapped[str] = mapped_column(String(24))
    to_ff_id: Mapped[int | None] = mapped_column(ForeignKey("fulfillment_centers.id"))

    __table_args__ = (CheckConstraint("qty > 0", name="ck_mv_qty_positive"),)
