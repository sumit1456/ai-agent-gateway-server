import asyncio, hashlib, secrets
import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from sqlmodel import select
from app.config import settings
from app.db import get_db
from app.models.tables import ApiKey, User

_fernet = Fernet(settings.encryption_key.encode())

def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()

def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def new_api_key() -> tuple[str, str, str]:
    """Returns (raw, hash, prefix). The raw key is shown to the user exactly once."""
    raw = "gw_" + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:8]

async def hash_password(pw: str) -> str:
    return (await asyncio.to_thread(bcrypt.hashpw, pw.encode(), bcrypt.gensalt())).decode()

async def check_password(pw: str, hashed: str) -> bool:
    return await asyncio.to_thread(bcrypt.checkpw, pw.encode(), hashed.encode())

async def current_user(authorization: str = Header(...), db=Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    key = (await db.exec(select(ApiKey).where(ApiKey.key_hash == hash_key(token)))).first()
    if not key:
        raise HTTPException(401, "Invalid API key")
    user = await db.get(User, key.user_id)
    if not user:
        raise HTTPException(401, "Invalid API key")
    return user