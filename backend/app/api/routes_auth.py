"""Auth + users routes."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.database import db, now_iso
from app.core.errors import DomainError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_principal,
    hash_password,
    verify_password,
)
from app.core.audit import audit
from app.core.rbac import ROLE_PERMISSIONS

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserIn(BaseModel):
    email: str
    password: str
    name: str
    roles: List[str]
    vendor_id: Optional[str] = None


@router.post("/login")
async def login(payload: LoginIn):
    user = await db.db.users.find_one({"email": payload.email.lower().strip()})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token(user["user_id"], user["roles"],
                                user.get("vendor_id"))
    refresh = create_refresh_token(user["user_id"])
    await audit("USER", user["user_id"], "LOGIN",
                {"type": "USER", "id": user["user_id"]})
    return {
        "access_token": token,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": {"user_id": user["user_id"], "email": user["email"],
                 "name": user["name"], "roles": user["roles"],
                 "vendor_id": user.get("vendor_id"),
                 "permissions": sorted(_perms(user["roles"]))},
    }


def _perms(roles: List[str]):
    out = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out


@router.post("/refresh")
async def refresh(payload: RefreshIn):
    data = decode_token(payload.refresh_token)
    if data.get("type") != "refresh":
        raise HTTPException(401, "Wrong token type")
    user = await db.db.users.find_one({"user_id": data["sub"]})
    if not user:
        raise HTTPException(401, "User gone")
    return {"access_token": create_access_token(user["user_id"], user["roles"],
                                                user.get("vendor_id")),
            "token_type": "bearer"}


@router.get("/me")
async def me(principal: dict = Depends(get_current_principal)):
    user = await db.db.users.find_one({"user_id": principal["id"]})
    if not user:
        raise HTTPException(404, "User not found")
    return {"user_id": user["user_id"], "email": user["email"],
            "name": user["name"], "roles": user["roles"],
            "vendor_id": user.get("vendor_id"),
            "permissions": sorted(_perms(user["roles"]))}


@users_router.get("")
async def list_users(principal: dict = Depends(get_current_principal)):
    rows = []
    async for u in db.db.users.find({}, {"password_hash": 0}):
        u.pop("_id", None)
        rows.append(u)
    return rows


@users_router.post("")
async def create_user(payload: UserIn,
                      principal: dict = Depends(get_current_principal)):
    if "SUPER_ADMIN" not in principal.get("roles", []):
        raise HTTPException(403, "Only SUPER_ADMIN creates users")
    if await db.db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(409, "Email exists")
    uid = await _next_uid()
    doc = {
        "user_id": uid,
        "email": payload.email.lower().strip(),
        "name": payload.name,
        "roles": payload.roles,
        "vendor_id": payload.vendor_id,
        "password_hash": hash_password(payload.password),
        "created_at": now_iso(),
    }
    await db.db.users.insert_one(doc)
    doc.pop("password_hash", None)
    return doc


async def _next_uid() -> str:
    d = await db.db.counters.find_one_and_update(
        {"name": "user"}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=True)
    return f"USR-{int(d['seq']):05d}"
