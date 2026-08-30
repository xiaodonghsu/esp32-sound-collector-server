from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClientConfig(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location: str | None = None
    type: str | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClientSelector(BaseModel):
    id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def require_exactly_one_selector(self) -> "ClientSelector":
        if (self.id is None) == (self.name is None):
            raise ValueError("必须且只能提供 id 或 name 中的一个")
        return self


class Parameter(BaseModel):
    para: str = Field(min_length=1)
    value: Any

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class ControlRequest(BaseModel):
    id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    mid: str | None = Field(default=None, min_length=1)
    cmd: Literal["start", "stop", "status", "set"]
    url: str | None = None
    segment: int | None = Field(default=None, gt=0)
    parameters: list[Parameter] | None = None

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_command(self) -> "ControlRequest":
        if (self.id is None) == (self.name is None):
            raise ValueError("必须且只能提供 id 或 name 中的一个")
        if self.cmd == "start" and not self.url:
            raise ValueError("cmd 为 start 时必须提供 url")
        if self.cmd == "set" and not self.parameters:
            raise ValueError("cmd 为 set 时必须提供非空 parameters")
        return self

