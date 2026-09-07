from __future__ import annotations

import base64
import binascii
import json
from typing import Any

import httpx

from app.settings import Settings


class EmqxError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class EmqxClientNotFoundError(EmqxError):
    """The client exists locally but is not currently known to EMQX."""

    def __init__(self) -> None:
        super().__init__("ESP32 客户端当前未连接到 EMQX", status_code=404)


class EmqxClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _auth(self) -> httpx.BasicAuth:
        if not self.settings.emqx_api_key or not self.settings.emqx_secret_key:
            raise EmqxError(
                "未配置 EMQX_API_KEY 或 EMQX_SECRET_KEY", status_code=503
            )
        return httpx.BasicAuth(
            self.settings.emqx_api_key, self.settings.emqx_secret_key
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.emqx_base_url,
                auth=self._auth(),
                timeout=self.settings.emqx_timeout_seconds,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise EmqxError("请求 EMQX 超时", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise EmqxError(f"无法连接 EMQX: {exc}") from exc

        if response.status_code == 404:
            try:
                error_data = response.json()
            except ValueError:
                error_data = None
            if isinstance(error_data, dict) and error_data.get("code") in {
                "CLIENTID_NOT_FOUND",
                # Also accept the spelling used by some integrations.
                "CLENTID_NOT_FOUND",
            }:
                raise EmqxClientNotFoundError
            raise EmqxError("ESP32 客户端当前未连接到 EMQX", status_code=404)
        if response.is_error:
            detail = response.text[:500]
            raise EmqxError(
                f"EMQX 返回 HTTP {response.status_code}: {detail}",
                status_code=502,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise EmqxError("EMQX 返回了无效的 JSON") from exc
        if not isinstance(data, dict):
            raise EmqxError("EMQX 返回格式不是 JSON 对象")
        return data

    async def get_client(self, client_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/clients/{client_id}")

    async def control(self, client_id: str, payload: dict[str, Any]) -> Any:
        request_topic = f"to/recorder/{client_id}"
        body = {
            "request": {
                "topic": request_topic,
                "response_topic": f"from/recorder/{client_id}",
                "request_id": request_topic,
                "qos": 0,
                "payload_encoding": "plain",
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
            "timeout": self.settings.emqx_sync_timeout,
        }
        result = await self._request(
            "POST", "/plugin_api/emqx_sync_request/request", json=body
        )
        response = result.get("response")
        if not isinstance(response, dict):
            raise EmqxError("EMQX 同步请求未返回设备响应")
        encoded = response.get("payload")
        if response.get("payload_encoding") != "base64" or not isinstance(encoded, str):
            raise EmqxError("EMQX 响应中缺少 Base64 编码的 payload")
        try:
            decoded_text = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise EmqxError("EMQX 响应中的 Base64 payload 无法解码") from exc
        try:
            return json.loads(decoded_text)
        except json.JSONDecodeError as exc:
            raise EmqxError("EMQX 响应中的 payload 不是有效 JSON") from exc
