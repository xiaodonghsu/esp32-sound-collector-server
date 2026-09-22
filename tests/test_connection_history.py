import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.connection_history import ConnectionHistory, ConnectionMonitor, RETENTION_MS, TOPICS
from app.emqx import EmqxClientNotFoundError
from app.main import create_app
from app.models import ClientConfig
from app.repository import ClientRepository
from app.settings import Settings

NOW = 1800000000000


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr("app.connection_history.time.time", lambda: NOW / 1000)
    return ConnectionHistory(tmp_path)


def record(history, event="connected", ts=NOW, username="Recorders", client_id="device-1"):
    history.record(
        f"$SYS/brokers/emqx@node/clients/{client_id}/{event}",
        json.dumps({"clientid": client_id, "username": username, "ts": ts,
                    "reason": "test", "conn_props": {"User-Property": {}}}).encode(),
    )


def test_filter_persistence_sort_and_retention(history):
    record(history, username="Other")
    assert not list(history.directory.glob("*.json"))
    record(history, ts=NOW - RETENTION_MS - 1)
    record(history, ts=NOW - 100)
    record(history, "disconnected", NOW)
    record(history, ts=NOW - 50)
    record(history, "disconnected", NOW)  # Duplicate delivery.
    stored = json.loads((history.directory / "device-1.json").read_text())
    assert len(stored) == 3
    recent = ConnectionHistory(history.directory).recent("device-1")
    assert [entry["ts"] for entry in recent] == [NOW, NOW - 50]
    assert recent[0]["event"] == "disconnected"
    assert recent[0]["conn_props"] == {"User-Property": {}}


def test_idle_cleanup_and_query_prune(history, monkeypatch):
    record(history)
    record(history, client_id="device-2")
    unrelated = history.directory / "unrelated.json"
    unrelated.write_text('{"important": true}')
    monkeypatch.setattr("app.connection_history.time.time", lambda: (NOW + RETENTION_MS + 1) / 1000)
    assert history.recent("device-1") == []
    assert json.loads((history.directory / "device-1.json").read_text()) == []
    history.cleanup()
    assert json.loads((history.directory / "device-2.json").read_text()) == []
    assert json.loads(unrelated.read_text()) == {"important": True}


@pytest.mark.parametrize("client_id", ["..", "CON", "foo\\bar", "C:foo", ".hidden"])
def test_unsafe_filename(history, client_id):
    with pytest.raises(ValueError):
        record(history, client_id=client_id)
    assert not list(history.directory.glob("*.json"))


@pytest.mark.parametrize("offline", [False, True])
@pytest.mark.parametrize("selector", ["id", "name"])
def test_api_history(history, offline, selector):
    class Emqx:
        async def get_client(self, client_id):
            if offline:
                raise EmqxClientNotFoundError()
            return {"connected": True}

    repository = ClientRepository(history.directory / "clients.yml")
    repository.upsert(ClientConfig(id="device-1", name="room"))
    record(history, ts=NOW - 2)
    record(history, "disconnected", NOW - 1)
    record(history)
    client = TestClient(create_app(repository=repository, emqx=Emqx()))
    response = client.get("/configure/client", params={selector: "device-1" if selector == "id" else "room"})
    assert response.status_code == 200
    device = response.json()["clients"][0]
    assert device["online"] is (not offline)
    assert [e["ts"] for e in device["connection_history"]] == [NOW, NOW - 1]
    assert "connection_history" not in client.get("/configure/client").json()["clients"][0]


def test_monitor_reconnect_malformed_message_and_lifecycle(history, monkeypatch):
    mqtt_client = Mock()
    mqtt_client.subscribe.return_value = (0, 1)
    monkeypatch.setattr("app.connection_history.mqtt.Client", Mock(return_value=mqtt_client))
    settings = Settings("http://localhost", "", "", 10, "5s", history.directory / "clients.yml")
    monitor = ConnectionMonitor(settings, history)
    mqtt_client.username_pw_set.assert_called_once_with("sys_recorders", "bestlink")
    for _ in range(2):
        monitor._on_connect(mqtt_client, None, None, SimpleNamespace(is_failure=False), None)
    assert mqtt_client.subscribe.call_count == 2
    mqtt_client.subscribe.assert_called_with(TOPICS)
    monitor._on_message(mqtt_client, None, SimpleNamespace(
        topic="$SYS/brokers/node/clients/device-1/connected", payload=b"invalid json"))
    record(history)
    assert len(history.recent("device-1")) == 1
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        mqtt_client.connect_async.assert_called_once_with("192.168.4.244", 1883, 60)
        mqtt_client.loop_start.assert_called_once()
    mqtt_client.disconnect.assert_called_once()
    mqtt_client.loop_stop.assert_called_once()
    assert not app.state.connection_monitor._cleanup_thread.is_alive()
