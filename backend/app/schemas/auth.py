"""认证相关 Pydantic 模型。"""
from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field, field_serializer, field_validator

# bcrypt 上限 72 字节（不是 72 字符）：中文/emoji 多字节字符按 UTF-8 字节数校验
BCRYPT_MAX_BYTES = 72


def normalize_email(v: str) -> str:
    """6R19：邮箱统一小写并去空白，避免大小写变体绕过唯一约束/身份混淆。"""
    return (v or "").strip().lower()


def utc_rfc3339(dt: datetime | None) -> str | None:
    """统一 UTC RFC3339 契约（P1-1）：naive 一律按 UTC 解释并输出带 Z 后缀。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # 字符数下限 8；字节上限见 validator
    nickname: str = Field(min_length=1, max_length=64)

    @field_validator("email", mode="before")
    @classmethod
    def _email_normalize(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("password")
    @classmethod
    def _password_max_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError("密码过长：UTF-8 编码不得超过 72 字节")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _email_normalize(cls, v: str) -> str:
        return normalize_email(v)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    email: str
    nickname: str
    platform: str
    rank: str
    subscription_tier: str
    subscription_expire: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("subscription_expire")
    def _ser_expire(self, dt: datetime | None) -> str | None:
        return utc_rfc3339(dt)


TokenResponse.model_rebuild()
