"""Тестовые данные из задания: один магазин, два ФФ, три артикула.

Начальные остатки заводятся обычными приходами, поэтому попадают в журнал
и проходят сверку /api/audit так же, как любые другие операции.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import service
from .models import FulfillmentCenter, Product

PRODUCTS = [("SKU-101", "Органайзер"), ("SKU-102", "Контейнер"), ("SKU-103", "Корзина")]
FFS = ["Север", "Юг"]
INITIAL = {  # артикул -> (Север, Юг)
    "SKU-101": (10, 6),
    "SKU-102": (5, 0),
    "SKU-103": (0, 4),
}


def seed_demo_data(session: Session) -> None:
    if session.scalars(select(Product).limit(1)).first():
        return  # уже заполнено, повторный старт ничего не дублирует
    session.add_all(Product(sku=sku, name=name) for sku, name in PRODUCTS)
    session.add_all(FulfillmentCenter(name=n) for n in FFS)
    session.commit()
    ff_ids = {f["name"]: f["id"] for f in service.list_ffs(session)}
    for sku, qtys in INITIAL.items():
        for name, qty in zip(FFS, qtys):
            if qty > 0:
                service.receive_goods(session, sku, ff_ids[name], qty, crid=f"seed:{sku}:{name}")
