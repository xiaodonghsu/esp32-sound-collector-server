from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from app.settings import Settings

logger = logging.getLogger("uvicorn.error.connections")
RETENTION_MS = 7 * 24 * 60 * 60 * 1000
TOPICS = [
    ("$SYS/brokers/+/clients/+/connected", 0),
    ("$SYS/brokers/+/clients/+/disconnected", 0),
]


class ConnectionHistory:
    """Atomic JSON event storage, shared by the API and MQTT threads."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.RLock()

    def _path(self, client_id: str) -> Path:
        # IDs become filenames; reject path separators and Windows special names.
        if (
            not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", client_id)
            or client_id.endswith(".")
            or client_id.split(".")[0].upper()
            in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)]}
        ):
            raise ValueError("客户端 ID 不能作为日志文件名")
        return self.directory / f"{client_id}.json"

    def _read(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or any(
            not isinstance(item, dict)
            or item.get("event") not in {"connected", "disconnected"}
            or item.get("clientid") != path.stem
            or item.get("username") != "Recorders"
            or type(item.get("ts")) is not int
            for item in records
        ):
            raise ValueError(f"无效的设备事件日志: {path.name}")
        return records

    def _write(self, path: Path, records: list[dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _prune(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = int(time.time() * 1000) - RETENTION_MS
        return sorted(
            (item for item in records if item["ts"] >= cutoff),
            key=lambda item: item["ts"], reverse=True,
        )

    def record(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if (len(parts) != 6 or parts[:2] != ["$SYS", "brokers"]
                or parts[3] != "clients" or parts[5] not in {"connected", "disconnected"}):
            return
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("username") != "Recorders":
            return
        if data.get("clientid") != parts[4]:
            raise ValueError("MQTT 主题与消息中的 clientid 不一致")
        if type(data.get("ts")) is not int or data["ts"] < 0:
            raise ValueError("MQTT 事件 ts 必须为有效的 Unix 毫秒时间戳")
        entry = {**data, "event": parts[5], "topic": topic}
        path = self._path(parts[4])
        with self._lock:
            records = self._read(path)
            if entry not in records:
                records.append(entry)
            self._write(path, self._prune(records))

    def recent(self, client_id: str) -> list[dict[str, Any]]:
        try:
            path = self._path(client_id)
        except ValueError:
            return []
        with self._lock:
            records = self._read(path)
            retained = self._prune(records)
            if retained != records:
                self._write(path, retained)
            return retained[:2]

    def cleanup(self) -> None:
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    records = self._read(path)
                    retained = self._prune(records)
                    if records != retained:
                        self._write(path, retained)
                except (OSError, ValueError):
                    logger.exception("无法清理日志 %s", path.name)


class ConnectionMonitor:
    def __init__(self, settings: Settings, history: ConnectionHistory) -> None:
        self.settings = settings
        self.history = history
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_subscribe = self._on_subscribe
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.enable_logger(logger)
        self._stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("MQTT 监测连接失败: %s", reason_code)
            return
        result, _ = client.subscribe(TOPICS)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.error("MQTT 监测订阅发送失败: %s", result)

    def _on_connect_fail(self, client, userdata):
        logger.warning("MQTT 监测连接失败，正在自动重试")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(code.is_failure for code in reason_codes):
            logger.error("MQTT 监测订阅被拒绝: %s", reason_codes)
        else:
            logger.info("已订阅设备上线、下线事件")

    def _on_message(self, client, userdata, message):
        try:
            self.history.record(message.topic, message.payload)
        except Exception:
            # A malformed message or storage failure must not kill the MQTT loop.
            logger.exception("无法记录 MQTT 上下线事件: %s", message.topic)

    def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.history.cleanup()
            except Exception:
                logger.exception("设备事件日志清理失败")
            self._stop.wait(60)

    def start(self) -> None:
        self._stop.clear()
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, 60)
        self.client.loop_start()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.client.disconnect()
        self.client.loop_stop()
        if self._cleanup_thread is not None:
            self._cleanup_thread.join()
