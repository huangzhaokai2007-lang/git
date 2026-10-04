"""F01 新增表单契约，不改 tools.schemas 的冻结模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CredentialsReq(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    card_no: str = Field(pattern=r"^[0-9]{16}$")
    password: SecretStr
    session_id: str = Field(min_length=1, max_length=128)


class ConfirmReq(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=16, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
