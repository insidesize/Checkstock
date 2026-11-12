import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import service
from .db import SessionLocal, engine, get_session
from .models import Base
from .seed import seed_demo_data

Qty = Field(gt=0, le=1_000_000, description="Количество единиц, целое > 0")
RequestId = Field(default=None, max_length=64, description="Ключ идемпотентности создания")


class FFIn(BaseModel):
    name: str


class ReceiptIn(BaseModel):
    sku: str
    ff_id: int
    qty: int = Qty
    client_request_id: str | None = RequestId


class TransferIn(BaseModel):
    sku: str
    from_ff_id: int
    to_ff_id: int
    qty: int = Qty
    client_request_id: str | None = RequestId


class FboIn(BaseModel):
    sku: str
    from_ff_id: int
    qty: int = Qty
    marketplace_warehouse: str = Field(min_length=1, max_length=200)
    client_request_id: str | None = RequestId


class FbsIn(BaseModel):
    sku: str
    ff_id: int
    qty: int = Qty
    order_ref: str = Field(min_length=1, max_length=200)
    client_request_id: str | None = RequestId


api = APIRouter(prefix="/api")
Db = Depends(get_session)


@api.get("/stock")
def get_stock(s: Session = Db):
    return service.stock_view(s)


@api.get("/ffs")
def get_ffs(s: Session = Db):
    return service.list_ffs(s)


@api.post("/ffs", status_code=201)
def create_ff(body: FFIn, s: Session = Db):
    return service.create_ff(s, body.name)


@api.patch("/ffs/{ff_id}")
def rename_ff(ff_id: int, body: FFIn, s: Session = Db):
    return service.rename_ff(s, ff_id, body.name)


@api.delete("/ffs/{ff_id}")
def delete_ff(ff_id: int, s: Session = Db):
    """Мягкое удаление: остатки и история сохраняются."""
    return service.archive_ff(s, ff_id)


@api.post("/ffs/{ff_id}/restore")
def restore_ff(ff_id: int, s: Session = Db):
    return service.restore_ff(s, ff_id)


@api.post("/receipts", status_code=201)
def receipt(b: ReceiptIn, s: Session = Db):
    return service.receive_goods(s, b.sku, b.ff_id, b.qty, b.client_request_id)


@api.post("/transfers", status_code=201)
def create_transfer(b: TransferIn, s: Session = Db):
    return service.create_transfer(s, b.sku, b.from_ff_id, b.to_ff_id, b.qty, b.client_request_id)


@api.post("/transfers/{doc_id}/receive")
def receive_transfer(doc_id: int, s: Session = Db):
    return service.receive_transfer(s, doc_id)


@api.post("/fbo", status_code=201)
def create_fbo(b: FboIn, s: Session = Db):
    return service.create_fbo(s, b.sku, b.from_ff_id, b.qty, b.marketplace_warehouse, b.client_request_id)


@api.post("/fbo/{doc_id}/accept")
def accept_fbo(doc_id: int, s: Session = Db):
    return service.accept_fbo(s, doc_id)


@api.post("/fbs", status_code=201)
def reserve_fbs(b: FbsIn, s: Session = Db):
    return service.reserve_fbs(s, b.sku, b.ff_id, b.qty, b.order_ref, b.client_request_id)


@api.post("/fbs/{doc_id}/ship")
def ship_fbs(doc_id: int, s: Session = Db):
    return service.ship_fbs(s, doc_id)


@api.post("/fbs/{doc_id}/cancel")
def cancel_fbs(doc_id: int, s: Session = Db):
    return service.cancel_fbs(s, doc_id)


@api.get("/documents")
def documents(kind: str | None = None, active_only: bool = False, s: Session = Db):
    return service.list_documents(s, kind, active_only)


@api.get("/movements")
def movements(sku: str | None = None, ff_id: int | None = None,
              limit: int = Query(200, ge=1, le=1000), s: Session = Db):
    return service.list_movements(s, sku, ff_id, limit)


@api.get("/audit")
def audit(s: Session = Db):
    """Пересчёт по журналу: сходятся ли остатки и документы."""
    return service.audit(s)


def init_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("select 1"))
            break
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    Base.metadata.create_all(engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if os.environ.get("SEED_ON_START", "1") == "1":
        with SessionLocal() as s:
            seed_demo_data(s)
    yield


app = FastAPI(title="CheckStock", lifespan=lifespan)


@app.exception_handler(service.DomainError)
async def domain_error_handler(_, exc: service.DomainError):
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message, **exc.extra}},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "VALIDATION", "message": f"Некорректные поля: {fields}"}},
    )


app.include_router(api)

# В Docker-образе собранный фронтенд лежит рядом; в разработке его отдаёт Vite.
static_dir = Path(os.environ.get("STATIC_DIR", "/app/static"))
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
