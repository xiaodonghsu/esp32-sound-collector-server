from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import yaml

from app.models import ClientConfig


class ClientNotFoundError(LookupError):
    pass


class ClientConflictError(ValueError):
    pass


class ClientRepository:
    """Thread-safe, atomic YAML-backed client configuration store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._write_unlocked([])

    def _read_unlocked(self) -> list[ClientConfig]:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"无法读取客户端配置文件 {self.path}: {exc}") from exc
        clients = raw.get("clients", [])
        if not isinstance(clients, list):
            raise RuntimeError("clients.yml 中的 clients 必须是列表")
        return [ClientConfig.model_validate(item) for item in clients]

    def _write_unlocked(self, clients: list[ClientConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": [
                client.model_dump(exclude_none=True, mode="json") for client in clients
            ]
        }
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                yaml.safe_dump(
                    payload, stream, allow_unicode=True, sort_keys=False
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def list(self) -> list[ClientConfig]:
        with self._lock:
            return self._read_unlocked()

    def get(self, *, client_id: str | None = None, name: str | None = None) -> ClientConfig:
        with self._lock:
            for client in self._read_unlocked():
                if client_id is not None and client.id == client_id:
                    return client
                if name is not None and client.name == name:
                    return client
        selector = f"id={client_id}" if client_id is not None else f"name={name}"
        raise ClientNotFoundError(f"客户端不存在: {selector}")

    def upsert(self, incoming: ClientConfig) -> tuple[ClientConfig, bool]:
        with self._lock:
            clients = self._read_unlocked()
            id_index = next(
                (index for index, item in enumerate(clients) if item.id == incoming.id),
                None,
            )
            name_index = next(
                (index for index, item in enumerate(clients) if item.name == incoming.name),
                None,
            )
            if name_index is not None and name_index != id_index:
                raise ClientConflictError(f"客户端名称已存在: {incoming.name}")
            created = id_index is None
            if created:
                clients.append(incoming)
            else:
                clients[id_index] = incoming
            self._write_unlocked(clients)
            return incoming, created

    def delete(self, *, client_id: str | None = None, name: str | None = None) -> ClientConfig:
        with self._lock:
            clients = self._read_unlocked()
            for index, client in enumerate(clients):
                if (client_id is not None and client.id == client_id) or (
                    name is not None and client.name == name
                ):
                    removed = clients.pop(index)
                    self._write_unlocked(clients)
                    return removed
        selector = f"id={client_id}" if client_id is not None else f"name={name}"
        raise ClientNotFoundError(f"客户端不存在: {selector}")

