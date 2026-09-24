"""Authentication: bcrypt password hashing, JWT issue/verify, FastAPI dependencies.

Principal model (shared by humans and AI agents):
    {"type": "USER"|"AGENT", "id": "...", "roles": [...], "vendor_id": optional}

Security hardening:
- short-lived access tokens, hashed refresh tokens with rotation + reuse detection
- token revocation via user password_changed_at epoch (`pwd_epoch`)
- login rate limiting + account lockout (Part 1)
- MFA-ready: TOTP provisioning/verification hooks used by routes_auth
"""
import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

log = logging.getLogger("pharmaos.security")

bearer_scheme = HTTPBearer(auto_error=False)

# --------------------------------------------------------------- rate limiting
# In-process sliding window keyed by (bucket, identity). Redis-backed when the
# cache is available (multi-worker safe); in-memory fallback otherwise.
import asyncio

_rate_lock = asyncio.Lock()
_rate_windows: dict = {}


async def rate_limit(bucket: str, identity: str, limit: int,
                     window_seconds: int = 60) -> bool:
    """True if allowed; False when over the limit in the sliding window."""
    try:
        from app.core.redis_client import cache

        if cache.is_redis:
            key = f"rl:{bucket}:{identity}"
            n = await cache.incr_window(key, window_seconds)
            return n <= limit
    except Exception:
        pass
    now = time.time()
    async with _rate_lock:
        key = (bucket, identity)
        q = [t for t in _rate_windows.get(key, []) if now - t < window_seconds]
        if len(q) >= limit:
            _rate_windows[key] = q
            return False
        q.append(now)
        _rate_windows[key] = q
        return True


def password_issues(password: str) -> List[str]:
    """Deterministic password policy — returns list of problems (empty = OK)."""
    issues = []
    if len(password or "") < settings.PASSWORD_MIN_LENGTH:
        issues.append(f"at least {settings.PASSWORD_MIN_LENGTH} characters")
    classes = sum([
        any(c.isupper() for c in password or ""),
        any(c.islower() for c in password or ""),
        any(c.isdigit() for c in password or ""),
        any(not c.isalnum() for c in password or ""),
    ])
    if classes < settings.PASSWORD_REQUIRE_CLASSES:
        issues.append(
            f"at least {settings.PASSWORD_REQUIRE_CLASSES} of upper/lower/digit/symbol")
    return issues


# ------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


# ------------------------------------------------------- TOTP (MFA-ready, stdlib)
def _totp_code(secret_b32: str, for_time: Optional[int] = None, step: int = 30,
               digits: int = 6) -> str:
    key = base64.b32decode(secret_b32, casefold=True)
    counter = int((for_time or time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) \
        % (10 ** digits)
    return str(code).zfill(digits)


def generate_mfa_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def verify_mfa(secret_b32: Optional[str], code: str,
               window: int = 1) -> bool:
    """Verify a TOTP code with ±1 step tolerance."""
    if not secret_b32 or not code:
        return False
    now = time.time()
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_totp_code(secret_b32, now + delta * 30), code):
            return True
    return False


def secret_bfa_guard(secret: Optional[str]) -> bool:
    return bool(secret)


# ---------------------------------------------------------------------- tokens
def _pwd_epoch(user: dict) -> int:
    """Security epoch: refresh tokens issued before a password change die."""
    ts = user.get("password_changed_at")
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            # stored UTC naive strings must not be read as local time
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return 0


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(subject: str, roles: List[str], vendor_id: Optional[str] = None,
                        site_ids: Optional[List[str]] = None,
                        customer_id: Optional[str] = None,
                        extra: Optional[dict] = None,
                        expires_minutes: Optional[int] = None) -> str:
    payload = {
        "sub": subject,
        "roles": roles,
        "vendor_id": vendor_id,
        "site_ids": site_ids,
        "customer_id": customer_id,
        "type": "access",
        "jti": secrets.token_hex(8),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc)
               + timedelta(minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, ttl_days: Optional[int] = None) -> tuple:
    """Returns (opaque_token, doc). The DB stores only the SHA-256 hash."""
    raw = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    doc = {
        "token_hash": token_fingerprint(raw),
        "type": "refresh",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days or settings.REFRESH_TOKEN_EXPIRE_DAYS)),
    }
    return raw, doc


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def decode_refresh(raw: str) -> Optional[dict]:
    """Opaque refresh tokens are DB-verified, not JWTs. Kept for legacy tokens."""
    try:
        return jwt.decode(raw, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None


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
        "site_ids": payload.get("site_ids"),
        "customer_id": payload.get("customer_id"),
        "jti": payload.get("jti"),
    }


def require_roles(*roles: str):
    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        if "SUPER_ADMIN" in principal["roles"]:
            return principal
        if not any(r in principal["roles"] for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires one of {roles}")
        return principal

    return _dep
