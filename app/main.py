from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.emqx import EmqxClient, EmqxClientNotFoundError, EmqxError
from app.models import ClientConfig, ClientSelector, ControlRequest
from app.repository import (
    ClientConflictError,
    ClientNotFoundError,
    ClientRepository,
)
from app.settings import Settings


def create_app(
    settings: Settings | None = None,
    repository: ClientRepository | None = None,
    emqx: EmqxClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    app = FastAPI(
        title="ESP32 Sound Collector Server",
        version="1.0.0",
        description="将上游 REST API 控制请求转换为面向 ESP32 录音设备的 MQTT 消息。",
    )
    app.state.repository = repository or ClientRepository(resolved_settings.clients_file)
    app.state.emqx = emqx or EmqxClient(resolved_settings)

    @app.exception_handler(ClientNotFoundError)
    async def client_not_found_handler(
        request: Request, exc: ClientNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ClientConflictError)
    async def client_conflict_handler(
        request: Request, exc: ClientConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(EmqxError)
    async def emqx_error_handler(request: Request, exc: EmqxError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/configure/client",
        tags=["configuration"],
        status_code=status.HTTP_200_OK,
        summary="添加或修改客户端",
        description="按客户端 ID 新增或更新设备配置；客户端 ID 和名称均不可重复。",
    )
    async def upsert_client(request: Request, client: ClientConfig) -> JSONResponse:
        saved, created = request.app.state.repository.upsert(client)
        return JSONResponse(
            status_code=201 if created else 200,
            content={
                "created": created,
                "client": saved.model_dump(exclude_none=True),
            },
        )

    @app.delete(
        "/configure/client",
        tags=["configuration"],
        summary="删除客户端",
        description="通过客户端 ID 或设备名称删除一个已有客户端。",
    )
    async def delete_client(
        request: Request,
        selector: Annotated[
            ClientSelector,
            Body(description="客户端选择条件，id 和 name 必须且只能提供一个。"),
        ],
    ) -> dict[str, Any]:
        removed = request.app.state.repository.delete(
            client_id=selector.id, name=selector.name
        )
        return {"deleted": True, "client": removed.model_dump(exclude_none=True)}

    @app.get(
        "/configure/client",
        tags=["configuration"],
        summary="查询客户端",
        description=(
            "提供 id 或 name 时返回单个客户端配置及 EMQX 实时连接状态；"
            "不提供时返回全部本地配置，但不查询实时状态。"
        ),
    )
    async def get_client(
        request: Request,
        id: Annotated[
            str | None,
            Query(
                min_length=1,
                description="ESP32 客户端 ID；查询单个设备时与 name 二选一。",
                examples=["2884856cbfa4"],
            ),
        ] = None,
        name: Annotated[
            str | None,
            Query(
                min_length=1,
                description="设备名称；查询单个设备时与 id 二选一。",
                examples=["meeting-root-411"],
            ),
        ] = None,
    ) -> dict[str, Any]:
        if id is None and name is None:
            clients = request.app.state.repository.list()
            return {
                "clients": [
                    client.model_dump(exclude_none=True) for client in clients
                ]
            }
        try:
            selector = ClientSelector(id=id, name=name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        local = request.app.state.repository.get(
            client_id=selector.id, name=selector.name
        )
        try:
            emqx_data = await request.app.state.emqx.get_client(local.id)
        except EmqxClientNotFoundError:
            return {
                "clients": [
                    {
                        **local.model_dump(exclude_none=True),
                        "online": False,
                    }
                ]
            }
        return {
            "clients": [
                {
                    **local.model_dump(exclude_none=True),
                    "online": True,
                    "status": emqx_data,
                }
            ]
        }

    @app.post(
        "/client/control",
        tags=["control"],
        summary="控制客户端",
        description=(
            "按客户端 ID 或设备名称向 ESP32 发送同步 MQTT 控制消息，"
            "等待设备响应并返回响应负载。start 命令的采样参数 segment、"
            "samplerate、bitrate 和 channel 通过 WebSocket URL 查询参数传递。"
        ),
    )
    async def control_client(
        request: Request, command: ControlRequest
    ) -> Any:
        client = request.app.state.repository.get(
            client_id=command.id, name=command.name
        )
        raw = command.model_dump(exclude_none=True)
        raw.pop("id", None)
        raw.pop("name", None)
        # segment is carried in the WebSocket URL. Drop the former top-level
        # field if an older caller still sends it as an extra parameter.
        raw.pop("segment", None)
        # mid is an internal MQTT correlation ID and must never be controlled by
        # the REST caller. This assignment also replaces a legacy mid supplied as
        # an extra field by an older client.
        raw["mid"] = str(uuid4())
        return await request.app.state.emqx.control(client.id, raw)

    return app


app = create_app()
