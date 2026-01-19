from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.models.schemas import UserProfile

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> UserProfile:
    """Get the current authenticated user's profile."""
    result = (
        supabase.table("profiles")
        .select("*")
        .eq("id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    return UserProfile(**result.data)
