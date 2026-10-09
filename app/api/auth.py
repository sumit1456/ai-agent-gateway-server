from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.security import hash_password, check_password, current_user, new_api_key
from app.db import get_db
from app.models.tables import User, ApiKey
from sqlmodel import select
import uuid

router = APIRouter()
security = HTTPBearer()

class RegisterIn(BaseModel):
    email: str
    password: str
    name: str | None = None
    username: str | None = None
    organization: str | None = None

class LoginIn(BaseModel):
    email: str
    password: str

@router.post("/register")
async def register(payload: RegisterIn, db=Depends(get_db)):
    """Register a new user."""
    # Check duplicate email
    result = await db.exec(select(User).where(User.email == payload.email))
    if result.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    # Fill sensible defaults when name/username are omitted
    local = payload.email.split("@")[0]
    if payload.username:
        if (await db.exec(select(User).where(User.username == payload.username))).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")
        username = payload.username
    else:
        username, n = local, 2
        while (await db.exec(select(User).where(User.username == username))).first():
            username = f"{local}-{n}"
            n += 1
    name = payload.name or username

    # Create new user
    hashed_password = await hash_password(payload.password)
    user = User(
        email=payload.email,
        username=username,
        name=name,
        organization=payload.organization,
        password_hash=hashed_password,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {
        "user_id": str(user.id),
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "message": "Registration successful. Please log in.",
    }

@router.post("/login")
async def login(payload: LoginIn, db=Depends(get_db)):
    """Login and return an API key (creates one if none exist)."""
    email, password = payload.email, payload.password
    result = await db.exec(select(User).where(User.email == email))
    user = result.first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    if not await check_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Check if user has any API keys
    result = await db.exec(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    existing_keys = result.all()
    
    # If no keys exist, create one automatically for the user
    if len(existing_keys) == 0:
        raw_key, key_hash, prefix = new_api_key()
        api_key = ApiKey(user_id=user.id, key_hash=key_hash, prefix=prefix)
        db.add(api_key)
        await db.commit()
        
        return {
            "user_id": str(user.id),
            "email": user.email,
            "username": user.username,
            "name": user.name,
            "api_key": raw_key,
            "api_key_prefix": prefix,
            "has_api_key": True,
            "is_new_key": True,
            "message": "Login successful. An API key has been generated for you.",
        }
    
    # User has existing keys - return the most recent one for authentication
    # Note: We can only return a key we just created. For existing keys, we can't retrieve
    # the plaintext version (they're hashed). So we need to create a new session key.
    raw_key, key_hash, prefix = new_api_key()
    api_key = ApiKey(user_id=user.id, key_hash=key_hash, prefix=prefix)
    db.add(api_key)
    await db.commit()
    
    # Keep only the 10 most recent keys per user
    result = await db.exec(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    for old in list(result.all())[10:]:
        await db.delete(old)
    await db.commit()
    
    return {
        "user_id": str(user.id),
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "api_key": raw_key,
        "api_key_prefix": prefix,
        "has_api_key": True,
        "is_new_key": False,
        "api_key_count": len(existing_keys) + 1,
        "message": "Login successful. A new session API key has been generated.",
    }

@router.post("/create-api-key")
async def create_api_key(user: User = Depends(current_user), db=Depends(get_db)):
    """Create a new API key for the authenticated user."""
    raw_key, key_hash, prefix = new_api_key()
    api_key = ApiKey(user_id=user.id, key_hash=key_hash, prefix=prefix)
    db.add(api_key)
    await db.commit()
    
    # Keep only the 10 most recent keys per user
    result = await db.exec(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    for old in list(result.all())[10:]:
        await db.delete(old)
    await db.commit()
    
    return {
        "api_key": raw_key,
        "prefix": prefix,
        "message": "API key created. Store it securely - it will only be shown once.",
    }

@router.get("/api-keys")
async def list_api_keys(user: User = Depends(current_user), db=Depends(get_db)):
    """List API key prefixes for the authenticated user."""
    result = await db.exec(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.all()
    return {
        "keys": [{"prefix": k.prefix, "created_at": k.created_at.isoformat()} for k in keys],
        "count": len(keys),
    }

# Note: The actual authentication is done via the current_user dependency
# which checks the API key in the Authorization header