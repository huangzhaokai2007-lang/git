"""模拟银行卡密码：不记录输入，PBKDF2 加盐与常量时间比较。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

PIN_ITERATIONS = 210_000
MAX_FAILURES = 5
LOCK_SECONDS = 300
TOKEN_SECONDS = 300


def hash_pin(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PIN_ITERATIONS)
    return salt, digest.hex()


def matches_pin(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_pin(password, salt)[1], expected)


def attempt_key(user_id: str, number: str) -> str:
    return hashlib.sha256(f"{user_id}:{number}".encode()).hexdigest()


def redact_chat_credentials(text: str) -> str:
    masked = re.sub(r"(?<!\d)(?:[0-9][ \t-]*){12,18}[0-9](?!\d)", "[卡号已隐藏]", text)
    return re.sub(r"(密码\s*(?:是|为|[:：])?\s*)[^\s，。；,;]+", r"\1[密码已隐藏]", masked)
