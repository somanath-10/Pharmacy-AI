"""Authentication: bcrypt password hashing, JWT issue/verify, FastAPI dependencies.

Principal model (shared by humans and AI agents):
    {"type": "USER"|"AGENT", "id": "...", "roles": [...], "vendor_id": optional}
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(subject: str, roles: List[str], vendor_id: Optional[str] = None) -> str:
    payload = {
        "sub": subject,
        "roles": roles,
        "vendor_id": vendor_id,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    payload = {
        "sub": subject,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return {
        "type": "USER",
        "id": payload["sub"],
        "roles": payload.get("roles", []),
        "vendor_id": payload.get("vendor_id"),
    }


def require_roles(*roles: str):
    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        if "SUPER_ADMIN" in principal["roles"]:
            return principal
        if not any(r in principal["roles"] for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires one of {roles}")
        return principal

    return _dep
