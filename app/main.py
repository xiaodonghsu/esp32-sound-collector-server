from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.emqx import EmqxClient, EmqxError
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

    @app.delete("/configure/client", tags=["configuration"])
    async def delete_client(
        request: Request, selector: Annotated[ClientSelector, Body()]
    ) -> dict[str, Any]:
        removed = request.app.state.repository.delete(
            client_id=selector.id, name=selector.name
        )
        return {"deleted": True, "client": removed.model_dump(exclude_none=True)}

    @app.get("/configure/client", tags=["configuration"])
    async def get_client(
        request: Request,
        id: Annotated[str | None, Query(min_length=1)] = None,
        name: Annotated[str | None, Query(min_length=1)] = None,
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
        emqx_data = await request.app.state.emqx.get_client(local.id)
        return {
            "clients": [
                {
                    **local.model_dump(exclude_none=True),
                    "status": emqx_data,
                }
            ]
        }

    @app.post("/client/control", tags=["control"])
    async def control_client(
        request: Request, command: ControlRequest
    ) -> Any:
        client = request.app.state.repository.get(
            client_id=command.id, name=command.name
        )
        raw = command.model_dump(exclude_none=True)
        raw.pop("id", None)
        raw.pop("name", None)
        raw["mid"] = command.mid or str(uuid4())
        if command.cmd == "start" and command.segment is None:
            raw["segment"] = 200
        return await request.app.state.emqx.control(client.id, raw)

    return app


app = create_app()
