import json
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from supabase import Client, create_client

from app.core.config import Settings, get_settings


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # User ID
    email: Optional[str] = None
    role: Optional[str] = None


class CurrentUser(BaseModel):
    """Current authenticated user."""

    id: UUID
    email: Optional[str] = None


# HTTP Bearer token scheme
security = HTTPBearer()


def get_supabase_client(settings: Settings = Depends(get_settings)) -> Client:
    """Get Supabase client instance based on environment."""
    return create_client(
        settings.get_active_supabase_url(),
        settings.get_active_supabase_key()
    )


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> TokenPayload:
    """Verify and decode JWT token from Supabase."""
    token = credentials.credentials

    try:
        # Parse the JWK from settings (use active JWT secret)
        jwk = json.loads(settings.get_active_supabase_jwt_secret())

        payload = jwt.decode(
            token,
            jwk,
            algorithms=["ES256"],
            audience="authenticated",
        )
        return TokenPayload(
            sub=payload.get("sub"),
            email=payload.get("email"),
            role=payload.get("role"),
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid JWT key configuration",
        )


def get_current_user(token: TokenPayload = Depends(verify_token)) -> CurrentUser:
    """Get current authenticated user from token."""
    if not token.sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identity",
        )
    return CurrentUser(id=UUID(token.sub), email=token.email)
