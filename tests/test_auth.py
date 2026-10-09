"""Auth endpoints must accept JSON bodies (the frontend posts JSON, not query params)."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import router  # noqa: F401  (ensures module imports)
from app.db import get_db
from app.main import app
from app.models.tables import ApiKey, User


@pytest.fixture()
def client(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(
                    sync_conn, tables=[User.__table__, ApiKey.__table__]
                )
            )

    asyncio.run(init())

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


def test_register_accepts_json_body(client):
    res = client.post(
        "/auth/register", json={"email": "dev@example.com", "password": "s3cret-pass"}
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["email"] == "dev@example.com"
    assert data["api_key"].startswith("gw_")
    assert data["prefix"] == data["api_key"][:8]
    assert data["user_id"]


def test_register_is_not_query_params(client):
    res = client.post(
        "/auth/register", params={"email": "q@example.com", "password": "x"}
    )
    assert res.status_code == 422


def test_register_duplicate_email(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "abc12345"})
    res = client.post("/auth/register", json={"email": "dup@example.com", "password": "abc12345"})
    assert res.status_code == 400
    assert res.json()["detail"] == "Email already registered"


def test_register_missing_field_returns_422(client):
    res = client.post("/auth/register", json={"email": "no-pass@example.com"})
    assert res.status_code == 422


def test_login_with_json_success(client):
    client.post("/auth/register", json={"email": "login@example.com", "password": "abc12345"})
    res = client.post("/auth/login", json={"email": "login@example.com", "password": "abc12345"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["email"] == "login@example.com"
    assert data["api_key_prefix"]


def test_login_wrong_password(client):
    client.post("/auth/register", json={"email": "login2@example.com", "password": "abc12345"})
    res = client.post("/auth/login", json={"email": "login2@example.com", "password": "wrong"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password"


def test_login_unknown_user(client):
    res = client.post("/auth/login", json={"email": "ghost@example.com", "password": "abc12345"})
    assert res.status_code == 401
