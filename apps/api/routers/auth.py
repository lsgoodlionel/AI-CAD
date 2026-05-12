"""认证 API — 登录 / 刷新 Token"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import verify_password, create_access_token, create_refresh_token, decode_token
from dependencies import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
async def login(body: LoginRequest, db=Depends(get_db)):
    user = await db.fetch_one(
        "SELECT id, hashed_password, role, display_name, is_active FROM users WHERE username=$1",
        body.username,
    )
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    # 更新最后登录时间
    await db.execute(
        "UPDATE users SET last_login_at=now() WHERE id=$1", user["id"]
    )

    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "display_name": user["display_name"],
    }
    return {
        "access_token": create_access_token(payload),
        "refresh_token": create_refresh_token(payload),
        "token_type": "bearer",
        "user": {
            "id": str(user["id"]),
            "role": user["role"],
            "display_name": user["display_name"],
        },
    }


@router.post("/refresh")
async def refresh_token(body: RefreshRequest):
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise ValueError("不是 Refresh Token")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh Token 无效或已过期")

    new_payload = {"sub": payload["sub"], "role": payload["role"], "display_name": payload.get("display_name", "")}
    return {
        "access_token": create_access_token(new_payload),
        "token_type": "bearer",
    }
