import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://checkstock:checkstock@localhost:5432/checkstock"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
# expire_on_commit=False: после commit() можно спокойно сериализовать объекты в ответ.
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session():
    # Сервисные функции коммитят сами; при исключении незакоммиченное откатывается на close().
    with SessionLocal() as session:
        yield session
