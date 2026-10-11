"""F06 界面私有契约，保留冻结的 T12 和 ToolResult。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LimitPreviewReq(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    card_id: str = Field(min_length=1, max_length=128)
    kind: Literal["fixed", "temporary", "daily"]
    target: int = Field(gt=0, le=10_000_000)
    days: int | None = Field(default=None, ge=1, le=90)
    session_id: str = Field(min_length=1, max_length=128)


class LimitSubmitReq(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    token: str = Field(min_length=16, max_length=128)
    otp: str = Field(pattern=r"^[0-9]{6}$")
    session_id: str = Field(min_length=1, max_length=128)
