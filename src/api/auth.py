"""Xác thực API key cho các route FastAPI."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


class APIKeyAuth:
    """Xác thực header X-API-Key so với khóa đã cấu hình."""

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def verify(self, key: str | None) -> bool:
        """Kiểm tra API key — trả về True nếu auth thành công hoặc auth bị tắt."""
        if not self._key:
            return True
        return bool(key) and secrets.compare_digest(key, self._key)

    async def __call__(self, key: str | None = Security(_API_KEY_HEADER)) -> None:
        if not self._key:
            return  # auth tắt (khóa rỗng được cấu hình)
        if not key or not secrets.compare_digest(key, self._key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
