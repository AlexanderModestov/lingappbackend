from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.models.schemas import (
    FlashcardResponse,
    FlashcardReview,
    FlashcardReviewResponse,
    ReviewQuality,
)

router = APIRouter(prefix="/cards", tags=["Flashcards"])


def calculate_next_review(
    current_stage: int, quality: ReviewQuality
) -> tuple[int, datetime]:
    """
    Calculate the next review stage and time based on SRS algorithm.

    Args:
        current_stage: Current learning stage (0 = new, 1-8 = learning)
        quality: User's self-assessment of recall

    Returns:
        Tuple of (new_stage, next_review_datetime)
    """
    now = datetime.now(timezone.utc)

    if quality == ReviewQuality.FORGOT:
        # Reset to stage 1, review in 10 minutes
        return (1, now + timedelta(minutes=10))

    # User knows the card - advance to next stage
    intervals = {
        0: timedelta(minutes=10),  # New -> Stage 1
        1: timedelta(days=1),  # Stage 1 -> Stage 2
        2: timedelta(days=3),  # Stage 2 -> Stage 3
        3: timedelta(days=7),  # Stage 3 -> Stage 4
        4: timedelta(days=14),  # Stage 4 -> Stage 5
    }

    new_stage = current_stage + 1

    if current_stage in intervals:
        delta = intervals[current_stage]
    else:
        # For stages 5+, use (stage * 2) days
        delta = timedelta(days=new_stage * 2)

    return (new_stage, now + delta)


@router.get("/review", response_model=List[FlashcardResponse])
async def get_cards_for_review(
    limit: int = 20,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> List[FlashcardResponse]:
    """Get flashcards that are due for review."""
    now = datetime.now(timezone.utc).isoformat()

    result = (
        supabase.table("flashcards")
        .select("*")
        .eq("user_id", str(current_user.id))
        .lte("next_review_at", now)
        .order("next_review_at", desc=False)
        .limit(limit)
        .execute()
    )

    return [FlashcardResponse(**card) for card in result.data]


@router.post("/{card_id}/review", response_model=FlashcardReviewResponse)
async def review_card(
    card_id: str,
    review: FlashcardReview,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> FlashcardReviewResponse:
    """Submit a review for a flashcard and update SRS data."""
    # Get the card and verify ownership
    result = (
        supabase.table("flashcards")
        .select("id, learning_stage")
        .eq("id", card_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flashcard not found",
        )

    current_stage = result.data["learning_stage"]
    new_stage, next_review = calculate_next_review(current_stage, review.quality)

    # Update the flashcard
    update_result = (
        supabase.table("flashcards")
        .update(
            {
                "learning_stage": new_stage,
                "next_review_at": next_review.isoformat(),
            }
        )
        .eq("id", card_id)
        .execute()
    )

    return FlashcardReviewResponse(
        id=card_id,
        learning_stage=new_stage,
        next_review_at=next_review,
    )


@router.get("", response_model=List[FlashcardResponse])
async def list_all_cards(
    material_id: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> List[FlashcardResponse]:
    """List all flashcards, optionally filtered by material."""
    query = (
        supabase.table("flashcards")
        .select("*")
        .eq("user_id", str(current_user.id))
    )

    if material_id:
        query = query.eq("material_id", material_id)

    result = query.order("created_at", desc=True).execute()

    return [FlashcardResponse(**card) for card in result.data]


@router.get("/stats")
async def get_review_stats(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
):
    """Get review statistics for the current user."""
    now = datetime.now(timezone.utc).isoformat()

    # Get total cards
    total_result = (
        supabase.table("flashcards")
        .select("id", count="exact")
        .eq("user_id", str(current_user.id))
        .execute()
    )

    # Get cards due for review
    due_result = (
        supabase.table("flashcards")
        .select("id", count="exact")
        .eq("user_id", str(current_user.id))
        .lte("next_review_at", now)
        .execute()
    )

    # Get cards by stage (new, learning, mastered)
    new_result = (
        supabase.table("flashcards")
        .select("id", count="exact")
        .eq("user_id", str(current_user.id))
        .eq("learning_stage", 0)
        .execute()
    )

    mastered_result = (
        supabase.table("flashcards")
        .select("id", count="exact")
        .eq("user_id", str(current_user.id))
        .gte("learning_stage", 5)
        .execute()
    )

    total = total_result.count or 0
    new_count = new_result.count or 0
    mastered = mastered_result.count or 0
    learning = total - new_count - mastered

    return {
        "total_cards": total,
        "due_for_review": due_result.count or 0,
        "new_cards": new_count,
        "learning": learning,
        "mastered": mastered,
    }
