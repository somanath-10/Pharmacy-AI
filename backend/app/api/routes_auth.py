"""Auth + users routes.

Part 1 hardening: login rate limiting, account lockout, refresh-token rotation
with reuse detection, logout/revocation, password reset, MFA enrollment,
password policy, audited role/permission changes.
"""
from datetime import datetime, timedelta, timezone
import secrets
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.core.audit import audit
from app.core.config import settings
from app.core.database import db, now_iso
from app.core.rbac import ROLE_PERMISSIONS
from app.core.security import (
    bearer_scheme as bearer_scheme_dep,
    create_access_token,
    decode_token,
    generate_mfa_secret,
    get_current_principal as get_current_principal_dep,
    hash_password,
    password_issues,
    rate_limit,
    token_fingerprint,
    verify_mfa,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class LoginIn(BaseModel):
    email: str
    password: str
    mfa_code: Optional[str] = None


class RefreshIn(BaseModel):
    refresh_token: str


class UserIn(BaseModel):
    email: str
    password: str
    name: str
    roles: List[str]
    vendor_id: Optional[str] = None
    site_ids: Optional[List[str]] = None
    customer_id: Optional[str] = None


class RoleChangeIn(BaseModel):
    roles: List[str]
    reason: str


def _perms(roles: List[str]):
    out = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out


def _user_public(user: dict) -> dict:
    return {"user_id": user["user_id"], "email": user["email"],
            "name": user["name"], "roles": user.get("roles", []),
            "vendor_id": user.get("vendor_id"),
            "site_ids": user.get("site_ids"),
            "customer_id": user.get("customer_id"),
            "mfa_enrolled": bool(user.get("mfa_secret")),
            "status": user.get("status", "ACTIVE"),
            "permissions": sorted(_perms(user.get("roles", [])))}


async def _optional_principal(
        credentials=Depends(bearer_scheme_dep)):
    """Logout works with or without a valid access token (best-effort)."""
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            return None
        return {"type": "USER", "id": payload["sub"],
                "roles": payload.get("roles", [])}
    except HTTPException:
        return None


async def _required_principal(principal: dict = Depends(get_current_principal_dep)):
    return principal


# ------------------------------------------------------------------------ login
@router.post("/login")
async def login(payload: LoginIn):
    email = payload.email.lower().strip()

    # per-identity + per-IP-ish rate limit (identity proxy until proxy headers trusted)
    if not await rate_limit("login", email, settings.RATE_LIMIT_LOGIN_PER_MIN):
        await audit("AUTH", email, "LOGIN_RATE_LIMITED",
                    {"type": "ANONYMOUS", "id": email})
        raise HTTPException(429, "Too many login attempts; slow down")

    user = await db.db.users.find_one({"email": email})

    # lockout check BEFORE verification work
    if user and _locked(user):
        await audit("AUTH", user["user_id"], "LOGIN_LOCKED",
                    {"type": "ANONYMOUS", "id": email},
                    details={"locked_until": user["locked_until"]})
        raise HTTPException(423, "Account locked; try again later or reset password")

    if not user or not verify_password(payload.password, user["password_hash"]):
        if user:
            await _record_failure(user)
        await audit("AUTH", email, "LOGIN_FAILED", {"type": "ANONYMOUS", "id": email})
        raise HTTPException(401, "Invalid credentials")

    # MFA: enforce when enrolled globally or the role mandates it
    must_mfa = settings.MFA_ENABLED or any(
        r in settings.MFA_ENFORCE_ROLES.split(",") for r in user.get("roles", []))
    if must_mfa and user.get("mfa_secret"):
        if not payload.mfa_code or not verify_mfa(user["mfa_secret"],
                                                  payload.mfa_code):
            await audit("AUTH", user["user_id"], "LOGIN_MFA_FAILED",
                        {"type": "ANONYMOUS", "id": email})
            raise HTTPException(401, "MFA code required or invalid")
    if must_mfa and not user.get("mfa_secret"):
        # Enrollment is mandatory for this role: no secret → no session.
        # Issuing tokens here silently bypassed the entire MFA control.
        await audit("AUTH", user["user_id"], "LOGIN_MFA_ENROLLMENT_REQUIRED",
                    {"type": "ANONYMOUS", "id": email})
        raise HTTPException(403, "MFA_ENROLLMENT_REQUIRED")

    if user.get("status") == "SUSPENDED":
        raise HTTPException(403, "Account suspended")

    await db.db.users.update_one({"user_id": user["user_id"]}, {"$set": {
        "failed_logins": 0, "locked_until": None, "last_login_at": now_iso()}})
    tokens = await _issue_tokens(user)
    await audit("AUTH", user["user_id"], "LOGIN",
                {"type": "USER", "id": user["user_id"]})
    return {"access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "token_type": "bearer", "user": _user_public(user)}


def _locked(user: dict) -> bool:
    lu = user.get("locked_until")
    if not lu:
        return False
    try:
        # locked_until is stored as a UTC isoformat string; parse it as UTC
        # explicitly — a naive datetime compared with an aware one raises
        # TypeError and would 500 every login for a locked account
        lu_dt = datetime.fromisoformat(str(lu))
        if lu_dt.tzinfo is None:
            lu_dt = lu_dt.replace(tzinfo=timezone.utc)
        return lu_dt > datetime.now(timezone.utc)
    except ValueError:
        return False


async def _record_failure(user: dict):
    failures = int(user.get("failed_logins") or 0) + 1
    upd = {"failed_logins": failures, "updated_at": now_iso()}
    if failures >= settings.MAX_LOGIN_FAILURES:
        upd["locked_until"] = (datetime.now(timezone.utc)
                               + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                               ).isoformat()
        upd["failed_logins"] = 0
        await audit("AUTH", user["user_id"], "ACCOUNT_LOCKED",
                    {"type": "SYSTEM", "id": "auth"},
                    details={"threshold": settings.MAX_LOGIN_FAILURES})
    await db.db.users.update_one({"user_id": user["user_id"]}, {"$set": upd})


async def _issue_tokens(user: dict) -> dict:
    """Access JWT + opaque rotating refresh token (hashed at rest)."""
    raw_refresh, doc = _new_refresh_doc(user)
    doc["user_id"] = user["user_id"]
    doc["rotated_from"] = None
    await db.db.refresh_tokens.insert_one(doc)
    access = create_access_token(user["user_id"], user.get("roles", []),
                                 user.get("vendor_id"), user.get("site_ids"),
                                 user.get("customer_id"))
    return {"access_token": access, "refresh_token": raw_refresh}


def _new_refresh_doc(user: dict):
    from app.core.security import create_refresh_token

    return create_refresh_token(user["user_id"])


# ---------------------------------------------------------------------- refresh
@router.post("/refresh")
async def refresh(payload: RefreshIn):
    if not payload.refresh_token:
        raise HTTPException(401, "Missing refresh token")
    doc = await db.db.refresh_tokens.find_one(
        {"token_hash": token_fingerprint(payload.refresh_token)})
    if not doc:
        # Unknown token: either fabricated or already-rotated replay.
        await audit("AUTH", "unknown", "REFRESH_REJECTED",
                    {"type": "ANONYMOUS", "id": "unknown"},
                    details={"reason": "token not found"})
        raise HTTPException(401, "Invalid refresh token")

    if doc.get("revoked_at"):
        # REUSE of a revoked (rotated) token → assume theft, kill the family.
        await db.db.refresh_tokens.update_many(
            {"user_id": doc["user_id"], "revoked_at": None},
            {"$set": {"revoked_at": now_iso(),
                      "revoked_reason": "reuse-detected"}})
        await audit("AUTH", doc["user_id"], "REFRESH_REUSE_DETECTED",
                    {"type": "SYSTEM", "id": "auth"},
                    details={"action": "all_user_refresh_tokens_revoked"})
        raise HTTPException(401, "Refresh token reuse detected; all sessions revoked")

    if doc.get("expires_at") and str(doc["expires_at"])[:19] < datetime.now(timezone.utc).isoformat()[:19]:
        raise HTTPException(401, "Refresh token expired")

    user = await db.db.users.find_one({"user_id": doc["user_id"]})
    if not user or user.get("status") == "SUSPENDED":
        raise HTTPException(401, "User gone or suspended")

    # password change epoch: pre-change refresh tokens are dead
    if _pwd_epoch(user) > _issued_epoch(doc):
        await audit("AUTH", user["user_id"], "REFRESH_REJECTED",
                    {"type": "ANONYMOUS", "id": user["user_id"]},
                    details={"reason": "password changed"})
        raise HTTPException(401, "Session invalidated by password change")

    # ROTATE: revoke old, issue new (grace window tolerates racing requests)
    now = datetime.now(timezone.utc).isoformat()
    grace = (datetime.now(timezone.utc)
             + timedelta(seconds=settings.REFRESH_ROTATION_GRACE_SECONDS)).isoformat()
    res = await db.db.refresh_tokens.update_one(
        {"_id": doc["_id"], "revoked_at": None},
        {"$set": {"revoked_at": now, "revoked_reason": "rotated",
                  "grace_until": grace}})
    if res.modified_count == 0:
        raise HTTPException(401, "Refresh token already used")

    raw_new, new_doc = _new_refresh_doc(user)
    new_doc.update({"user_id": user["user_id"],
                    "rotated_from": doc["token_hash"][:12]})
    await db.db.refresh_tokens.insert_one(new_doc)
    access = create_access_token(user["user_id"], user.get("roles", []),
                                 user.get("vendor_id"), user.get("site_ids"),
                                 user.get("customer_id"))
    await audit("AUTH", user["user_id"], "TOKEN_REFRESHED",
                {"type": "USER", "id": user["user_id"]})
    return {"access_token": access, "refresh_token": raw_new,
            "token_type": "bearer"}


def _utc_epoch(ts) -> float:
    """Parse a stored UTC isoformat timestamp to a POSIX epoch.
    [:19] stripping would drop the +00:00 offset and make Python read the
    value as *local* time — a timezone-dependent token-validation bug."""
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _pwd_epoch(user: dict) -> float:
    ts = user.get("password_changed_at")
    if not ts:
        return 0.0
    return _utc_epoch(ts)


def _issued_epoch(doc: dict) -> float:
    ts = doc.get("issued_at")
    if not ts:
        return 0.0
    return _utc_epoch(ts)


# ----------------------------------------------------------------------- logout
@router.post("/logout")
async def logout(payload: RefreshIn,
                 principal: dict = Depends(_optional_principal)):
    if payload.refresh_token:
        await db.db.refresh_tokens.update_one(
            {"token_hash": token_fingerprint(payload.refresh_token)},
            {"$set": {"revoked_at": now_iso(), "revoked_reason": "logout"}})
    if principal:
        await audit("AUTH", principal["id"], "LOGOUT",
                    {"type": "USER", "id": principal["id"]})
    return {"ok": True}


# --------------------------------------------------------------- password reset
class ResetRequestIn(BaseModel):
    email: str


class ResetConfirmIn(BaseModel):
    email: str
    token: str
    new_password: str


@router.post("/password/reset-request")
async def password_reset_request(payload: ResetRequestIn):
    email = payload.email.lower().strip()
    user = await db.db.users.find_one({"email": email})
    if not await rate_limit("pwreset", email, 5):
        raise HTTPException(429, "Too many reset requests")
    if user:
        token = secrets.token_urlsafe(32)
        await db.db.password_resets.insert_one({
            "email": email,
            "token_hash": token_fingerprint(token),
            # BSON Date (not ISO string): Mongo TTL indexes only work on
            # real date fields.
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
            "used": False,
            "created_at": now_iso()})
        from app.core.notifications import notify

        await notify("PASSWORD_RESET_REQUESTED",
                     "Password reset requested",
                     f"Use this token to reset your password (valid 30 min): {token}",
                     [email], "USER", user["user_id"])
        await audit("AUTH", user["user_id"], "PASSWORD_RESET_REQUESTED",
                    {"type": "ANONYMOUS", "id": email})
    # Always 200 — never leak account existence
    return {"ok": True, "detail": "If the account exists, a reset token was sent"}


@router.post("/password/reset-confirm")
async def password_reset_confirm(payload: ResetConfirmIn):
    email = payload.email.lower().strip()
    doc = await db.db.password_resets.find_one_and_update(
        {"email": email, "token_hash": token_fingerprint(payload.token),
         "used": False,
         "expires_at": {"$gt": datetime.now(timezone.utc)}},
        {"$set": {"used": True, "used_at": now_iso()}})
    if not doc:
        raise HTTPException(400, "Invalid or expired reset token")
    issues = password_issues(payload.new_password)
    if issues:
        raise HTTPException(422, f"Password policy: {', '.join(issues)}")
    user = await db.db.users.find_one({"email": email})
    if not user:
        raise HTTPException(400, "Invalid reset token")
    new_epoch = datetime.now(timezone.utc).isoformat()
    await db.db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"password_hash": hash_password(payload.new_password),
                  "password_changed_at": new_epoch,
                  "failed_logins": 0, "locked_until": None}})
    # revoke every refresh token (all sessions) on password reset
    await db.db.refresh_tokens.update_many(
        {"user_id": user["user_id"], "revoked_at": None},
        {"$set": {"revoked_at": now_iso(), "revoked_reason": "password-reset"}})
    await audit("AUTH", user["user_id"], "PASSWORD_RESET_COMPLETED",
                {"type": "USER", "id": user["user_id"]})
    return {"ok": True}


# -------------------------------------------------------------------------- MFA
class MFAEnrollIn(BaseModel):
    code: str


@router.post("/mfa/enroll")
async def mfa_enroll(principal: dict = Depends(_required_principal)):
    secret = generate_mfa_secret()
    await db.db.users.update_one({"user_id": principal["id"]},
                                 {"$set": {"mfa_secret_pending": secret}})
    await audit("AUTH", principal["id"], "MFA_ENROLL_STARTED",
                {"type": "USER", "id": principal["id"]})
    return {"secret": secret,
            "otpauth": f"otpauth://totp/PharmaOS:{principal['id']}?secret={secret}",
            "detail": "Confirm with /api/auth/mfa/activate using a code from your app"}


@router.post("/mfa/activate")
async def mfa_activate(payload: MFAEnrollIn,
                       principal: dict = Depends(_required_principal)):
    user = await db.db.users.find_one({"user_id": principal["id"]})
    pending = user.get("mfa_secret_pending") if user else None
    if not pending or not verify_mfa(pending, payload.code):
        raise HTTPException(400, "Invalid MFA code")
    await db.db.users.update_one({"user_id": principal["id"]},
                                 {"$set": {"mfa_secret": pending,
                                           "mfa_secret_pending": None}})
    await audit("AUTH", principal["id"], "MFA_ACTIVATED",
                {"type": "USER", "id": principal["id"]})
    return {"ok": True}


@router.post("/mfa/verify")
async def mfa_verify(payload: MFAEnrollIn,
                     principal: dict = Depends(_required_principal)):
    user = await db.db.users.find_one({"user_id": principal["id"]})
    if not user or not user.get("mfa_secret") \
            or not verify_mfa(user["mfa_secret"], payload.code):
        raise HTTPException(400, "Invalid MFA code")
    return {"ok": True}


# ------------------------------------------------------------------------ users
@router.get("/me")
async def me(principal: dict = Depends(_required_principal)):
    user = await db.db.users.find_one({"user_id": principal["id"]})
    if not user:
        raise HTTPException(404, "User not found")
    return _user_public(user)


@users_router.get("")
async def list_users(principal: dict = Depends(_required_principal)):
    if "SUPER_ADMIN" not in principal.get("roles", []):
        raise HTTPException(403, "Only SUPER_ADMIN lists users")
    rows = []
    async for u in db.db.users.find({}, {"password_hash": 0, "mfa_secret": 0,
                                         "mfa_secret_pending": 0}):
        u.pop("_id", None)
        rows.append(u)
    return rows


@users_router.post("")
async def create_user(payload: UserIn,
                      principal: dict = Depends(_required_principal)):
    if "SUPER_ADMIN" not in principal.get("roles", []):
        raise HTTPException(403, "Only SUPER_ADMIN creates users")
    issues = password_issues(payload.password)
    if issues:
        raise HTTPException(422, f"Password policy: {', '.join(issues)}")
    if await db.db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(409, "Email exists")
    uid = await _next_uid()
    doc = {
        "user_id": uid,
        "email": payload.email.lower().strip(),
        "name": payload.name,
        "roles": payload.roles,
        "vendor_id": payload.vendor_id,
        "site_ids": payload.site_ids,
        "customer_id": payload.customer_id,
        "password_hash": hash_password(payload.password),
        "status": "ACTIVE",
        "failed_logins": 0,
        "created_at": now_iso(),
    }
    await db.db.users.insert_one(doc)
    await audit("USER", uid, "CREATED", {"type": "USER", "id": principal["id"]},
                details={"roles": payload.roles})
    doc.pop("password_hash", None)
    return doc


@users_router.post("/{user_id}/roles")
async def change_roles(user_id: str, payload: RoleChangeIn,
                       principal: dict = Depends(_required_principal)):
    """Audited role change. SUPER_ADMIN grants require a second SUPER_ADMIN."""
    actor_roles = principal.get("roles", [])
    if "SUPER_ADMIN" not in actor_roles:
        raise HTTPException(403, "Only SUPER_ADMIN changes roles")
    target = await db.db.users.find_one({"user_id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if "SUPER_ADMIN" in payload.roles and "SUPER_ADMIN" not in target.get("roles", []) \
            and principal["id"] == target["user_id"]:
        raise HTTPException(403, "Cannot grant yourself SUPER_ADMIN")
    old = target.get("roles", [])
    await db.db.users.update_one({"user_id": user_id},
                                 {"$set": {"roles": payload.roles,
                                           "updated_at": now_iso()}})
    await audit("USER", user_id, "ROLE_CHANGED",
                {"type": "USER", "id": principal["id"]},
                previous_state=",".join(old), new_state=",".join(payload.roles),
                reason=payload.reason)
    # privilege change → drop existing sessions
    await db.db.refresh_tokens.update_many(
        {"user_id": user_id, "revoked_at": None},
        {"$set": {"revoked_at": now_iso(), "revoked_reason": "roles-changed"}})
    return {"ok": True, "roles": payload.roles}


@users_router.post("/{user_id}/status")
async def set_user_status(user_id: str, payload: dict = Body(default={}),
                          principal: dict = Depends(_required_principal)):
    """Suspend/activate a user (SUPER_ADMIN only, audited)."""
    if "SUPER_ADMIN" not in principal.get("roles", []):
        raise HTTPException(403, "Only SUPER_ADMIN sets status")
    status = payload.get("status")
    if status not in ("ACTIVE", "SUSPENDED"):
        raise HTTPException(422, "status must be ACTIVE|SUSPENDED")
    await db.db.users.update_one({"user_id": user_id},
                                 {"$set": {"status": status}})
    await audit("USER", user_id, f"STATUS_{status}",
                {"type": "USER", "id": principal["id"]})
    if status == "SUSPENDED":
        await db.db.refresh_tokens.update_many(
            {"user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": now_iso(), "revoked_reason": "suspended"}})
    return {"ok": True}


async def _next_uid() -> str:
    d = await db.db.counters.find_one_and_update(
        {"name": "user"}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=True)
    return f"USR-{int(d['seq']):05d}"
