import os

# Тесты ходят в реальный Postgres
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://checkstock:checkstock@localhost:5432/checkstock_test"
)
os.environ["SEED_ON_START"] = "0"
os.environ["STATIC_DIR"] = "/nonexistent"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db import DATABASE_URL, SessionLocal, engine
from app.main import app
from app.models import Base
from app.seed import seed_demo_data


def _ensure_test_database() -> None:
    url = make_url(DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("select 1 from pg_database where datname = :n"), {"n": url.database}).first()
        if not exists:
            conn.execute(text(f'create database "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def schema():
    _ensure_test_database()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def fresh_data(schema):
    """Каждый тест стартует с данными из задания: Север/Юг, SKU-101..103."""
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE movements, documents, stock, products, fulfillment_centers RESTART IDENTITY CASCADE"
        ))
    with SessionLocal() as s:
        seed_demo_data(s)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def ff(client):
    return {f["name"]: f["id"] for f in client.get("/api/ffs").json()}


class Stocks:
    """Короткий доступ к остаткам и сверка аудитом."""

    def __init__(self, client):
        self.c = client

    def row(self, sku):
        data = self.c.get("/api/stock").json()
        return next(r for r in data["rows"] if r["sku"] == sku)

    def at(self, sku, ff_id):
        return self.row(sku)["per_ff"][str(ff_id)]

    def audit_ok(self):
        res = self.c.get("/api/audit").json()
        assert res["ok"], res["problems"]


@pytest.fixture
def stocks(client):
    return Stocks(client)
