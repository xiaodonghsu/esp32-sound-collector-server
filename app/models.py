from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClientConfig(BaseModel):
    id: str = Field(
        min_length=1,
        description="ESP32 客户端 ID；已存在时更新该客户端，不存在时新增客户端。",
        examples=["2884856cbfa4"],
    )
    name: str = Field(
        min_length=1,
        description="设备名称，必须在所有客户端中唯一。",
        examples=["meeting-root-411"],
    )
    location: str | None = Field(
        default=None,
        description="设备部署位置。",
        examples=["4 楼 411 会议室"],
    )
    type: str | None = Field(
        default=None,
        description="设备或录音模组类型。",
        examples=["XVF3800"],
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClientSelector(BaseModel):
    id: str | None = Field(
        default=None,
        min_length=1,
        description="按 ESP32 客户端 ID 选择设备；与 name 必须且只能提供一个。",
        examples=["2884856cbfa4"],
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        description="按设备名称选择设备；与 id 必须且只能提供一个。",
        examples=["meeting-root-411"],
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def require_exactly_one_selector(self) -> "ClientSelector":
        if (self.id is None) == (self.name is None):
            raise ValueError("必须且只能提供 id 或 name 中的一个")
        return self


class Parameter(BaseModel):
    para: str = Field(
        min_length=1,
        description="需要设置的 ESP32 或 XVF3800 参数名称。",
        examples=["LED_BRIGHTNESS"],
    )
    value: Any = Field(
        description="参数值，类型由具体参数决定。",
        examples=[50],
    )

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class ControlRequest(BaseModel):
    id: str | None = Field(
        default=None,
        min_length=1,
        description="目标 ESP32 客户端 ID；与 name 必须且只能提供一个。",
        examples=["2884856cbfa4"],
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        description="目标设备名称；与 id 必须且只能提供一个。",
        examples=["meeting-root-411"],
    )
    mid: str | None = Field(
        default=None,
        min_length=1,
        description="消息 ID，用于关联请求和设备响应；不提供时由服务端生成 UUID。",
        examples=["6F9619FF-8B86-D011-B42D-00C04FC964FF"],
    )
    cmd: Literal["start", "stop", "status", "set"] = Field(
        description="控制命令：start 启动录音，stop 停止录音，status 获取状态，set 设置参数。",
        examples=["status"],
    )
    url: str | None = Field(
        default=None,
        description="音频上传 WebSocket 地址；cmd 为 start 时必填。",
        examples=["ws://192.168.4.250:10345/v1/recorder?id=meeting-001"],
    )
    segment: int | None = Field(
        default=None,
        gt=0,
        description="语音采集分包时长，单位为毫秒；cmd 为 start 且未提供时默认为 200。",
        examples=[200],
    )
    parameters: list[Parameter] | None = Field(
        default=None,
        description="参数设置列表；cmd 为 set 时必须提供非空列表。",
        examples=[
            [
                {"para": "LED_EFFECT", "value": 1},
                {"para": "LED_BRIGHTNESS", "value": 50},
            ]
        ],
    )

    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        json_schema_extra={
            "description": "控制字段之外的额外参数也会原样加入发送给设备的 MQTT 负载。"
        },
    )

    @model_validator(mode="after")
    def validate_command(self) -> "ControlRequest":
        if (self.id is None) == (self.name is None):
            raise ValueError("必须且只能提供 id 或 name 中的一个")
        if self.cmd == "start" and not self.url:
            raise ValueError("cmd 为 start 时必须提供 url")
        if self.cmd == "set" and not self.parameters:
            raise ValueError("cmd 为 set 时必须提供非空 parameters")
        return self
