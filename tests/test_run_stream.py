"""Coverage for authenticated Redis-backed run SSE streaming."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.runs import get_db
from app.main import app
from app.models.tables import Run
from app.security import current_user


class FakePubSub:
    def __init__(self, messages=None):
        self.channels = []
        self.closed = False
        self.messages = list(messages or [])

    async def subscribe(self, channel):
        self.channels.append(channel)

    async def unsubscribe(self, channel):
        self.channels.remove(channel)

    async def get_message(self, **_kwargs):
        return self.messages.pop(0) if self.messages else None

    async def aclose(self):
        self.closed = True


class FakeRedis:
    def __init__(self, messages=None):
        self.pubsub_client = FakePubSub(messages)

    def pubsub(self):
        return self.pubsub_client


class FakeDatabase:
    def __init__(self, run):
        self.run = run

    async def exec(self, _query):
        return SimpleNamespace(first=lambda: self.run)


@pytest.fixture
def stream_client(monkeypatch):
    user_id = uuid4()
    run_id = uuid4()
    fake_db = FakeDatabase(SimpleNamespace(
        id=run_id,
        user_id=user_id,
        status="done",
        result={"answer": "finished"},
        stop_reason="completed",
    ))

    async def override_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=user_id)
    client = TestClient(app)
    yield client, run_id, fake_db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(current_user, None)


def test_stream_returns_terminal_events_and_closes_pubsub(stream_client, monkeypatch):
    client, run_id, _fake_db = stream_client
    redis_client = FakeRedis()

    async def get_fake_redis():
        return redis_client

    monkeypatch.setattr("app.redis_client.get_redis", get_fake_redis)

    response = client.get(f"/v1/runs/{run_id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: connected" in response.text
    assert 'event: run_completed\ndata: {"status": "done"' in response.text
    assert redis_client.pubsub_client.closed
    assert redis_client.pubsub_client.channels == []


def test_stream_returns_503_when_redis_is_unavailable(stream_client, monkeypatch):
    client, run_id, _fake_db = stream_client

    async def no_redis():
        return None

    monkeypatch.setattr("app.redis_client.get_redis", no_redis)

    response = client.get(f"/v1/runs/{run_id}/stream")

    assert response.status_code == 503
    assert "polling endpoint" in response.json()["detail"]


def test_stream_rejects_invalid_run_id(stream_client):
    client, _, _fake_db = stream_client

    response = client.get("/v1/runs/not-a-uuid/stream")

    assert response.status_code == 400


def test_stream_does_not_expose_another_users_run(stream_client):
    client, run_id, _fake_db = stream_client
    app.dependency_overrides[get_db] = lambda: FakeDatabase(None)

    response = client.get(f"/v1/runs/{run_id}/stream")

    assert response.status_code == 404


def test_stream_forwards_live_redis_events(stream_client, monkeypatch):
    client, run_id, fake_db = stream_client
    fake_db.run.status = "running"
    redis_client = FakeRedis([{
        "type": "message",
        "data": json.dumps({"type": "node_enter", "data": {"node": "planner"}}),
    }, {
        "type": "message",
        "data": json.dumps({"type": "run_completed", "data": {"output": "ok"}}),
    }])

    async def get_fake_redis():
        return redis_client

    monkeypatch.setattr("app.redis_client.get_redis", get_fake_redis)

    response = client.get(f"/v1/runs/{run_id}/stream")

    assert response.status_code == 200
    assert 'event: connected\ndata: {"run_id":' in response.text
    assert 'event: node_enter\ndata: {"node": "planner"}' in response.text
    assert redis_client.pubsub_client.closed
