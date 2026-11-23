import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db import Base
from app.models import SourceAccess


@pytest.fixture
def test_engine():
    pg_url = os.environ.get("TEST_POSTGRES_URL")
    if pg_url:
        admin = create_engine(pg_url)
        schema = "test_san1c_" + uuid4().hex
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
        engine = create_engine(pg_url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        with NamedTemporaryFile(suffix=".sqlite3", delete=False) as file:
            filename = Path(file.name)
        engine = create_engine(f"sqlite:///{filename}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(SourceAccess.__table__.insert().values(id=1, managed=False))
        yield engine
    finally:
        engine.dispose()
        if pg_url:
            with admin.begin() as conn:
                conn.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        else:
            filename.unlink(missing_ok=True)
