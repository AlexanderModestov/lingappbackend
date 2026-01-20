import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from supabase import Client

from app.core.config import Settings

logger = logging.getLogger(__name__)


def get_or_create_subscription(user_id: UUID, supabase: Client) -> dict:
    """Get user's subscription, creating a free one if it doesn't exist."""
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("user_id", str(user_id))
        .single()
        .execute()
    )

    if result.data:
        return result.data

    # Create free subscription for new user
    now = datetime.now(timezone.utc)
    new_subscription = {
        "user_id": str(user_id),
        "status": "free",
        "uploads_this_week": 0,
        "week_reset_at": (now + timedelta(days=7)).isoformat(),
    }

    create_result = (
        supabase.table("subscriptions")
        .insert(new_subscription)
        .execute()
    )

    return create_result.data[0]


def get_user_tier(subscription: dict) -> Literal["free", "pro"]:
    """Determine user's tier based on subscription status."""
    status = subscription.get("status", "free")
    if status in ("trialing", "active", "past_due"):
        return "pro"
    return "free"


def maybe_reset_weekly_usage(subscription: dict, supabase: Client) -> dict:
    """Reset weekly usage if the reset time has passed. Returns updated subscription."""
    week_reset_at = subscription.get("week_reset_at")
    if not week_reset_at:
        return subscription

    # Parse the reset time
    if isinstance(week_reset_at, str):
        reset_time = datetime.fromisoformat(week_reset_at.replace("Z", "+00:00"))
    else:
        reset_time = week_reset_at

    now = datetime.now(timezone.utc)

    if now >= reset_time:
        # Reset usage and set next reset time
        new_reset_at = now + timedelta(days=7)

        result = (
            supabase.table("subscriptions")
            .update({
                "uploads_this_week": 0,
                "week_reset_at": new_reset_at.isoformat(),
            })
            .eq("id", subscription["id"])
            .execute()
        )

        return result.data[0]

    return subscription


def check_upload_limit(user_id: UUID, supabase: Client, settings: Settings) -> tuple[bool, int, int]:
    """
    Check if user can upload.
    Returns: (can_upload, current_count, limit)
    """
    subscription = get_or_create_subscription(user_id, supabase)
    subscription = maybe_reset_weekly_usage(subscription, supabase)

    tier = get_user_tier(subscription)
    limit = settings.pro_uploads_per_week if tier == "pro" else settings.free_uploads_per_week
    current = subscription.get("uploads_this_week", 0)

    return current < limit, current, limit


def increment_upload_count(user_id: UUID, supabase: Client) -> None:
    """Increment the user's weekly upload count."""
    subscription = get_or_create_subscription(user_id, supabase)

    supabase.table("subscriptions").update({
        "uploads_this_week": subscription.get("uploads_this_week", 0) + 1,
    }).eq("id", subscription["id"]).execute()


def check_quiz_limit(
    user_id: UUID,
    material_id: str,
    supabase: Client,
    settings: Settings
) -> tuple[bool, int, int]:
    """
    Check if user can create a quiz for the material.
    Returns: (can_create, current_count, limit)
    """
    subscription = get_or_create_subscription(user_id, supabase)
    tier = get_user_tier(subscription)
    limit = settings.pro_quizzes_per_material if tier == "pro" else settings.free_quizzes_per_material

    # Get current quiz count for material
    result = (
        supabase.table("materials")
        .select("quiz_count")
        .eq("id", material_id)
        .single()
        .execute()
    )

    if not result.data:
        return False, 0, limit

    current = result.data.get("quiz_count", 0)
    return current < limit, current, limit


def increment_quiz_count(material_id: str, supabase: Client) -> None:
    """Increment the quiz count for a material."""
    # Get current count
    result = (
        supabase.table("materials")
        .select("quiz_count")
        .eq("id", material_id)
        .single()
        .execute()
    )

    current = result.data.get("quiz_count", 0) if result.data else 0

    supabase.table("materials").update({
        "quiz_count": current + 1,
    }).eq("id", material_id).execute()


def check_chat_access(user_id: UUID, supabase: Client) -> bool:
    """Check if user has chat access (Pro tier only)."""
    subscription = get_or_create_subscription(user_id, supabase)
    tier = get_user_tier(subscription)
    return tier == "pro"


def get_subscription_response(user_id: UUID, supabase: Client, settings: Settings) -> dict:
    """Get full subscription status for API response."""
    subscription = get_or_create_subscription(user_id, supabase)
    subscription = maybe_reset_weekly_usage(subscription, supabase)

    tier = get_user_tier(subscription)

    return {
        "status": subscription.get("status", "free"),
        "tier": tier,
        "trial_end": subscription.get("trial_end"),
        "current_period_end": subscription.get("current_period_end"),
        "uploads_used": subscription.get("uploads_this_week", 0),
        "uploads_limit": settings.pro_uploads_per_week if tier == "pro" else settings.free_uploads_per_week,
        "quizzes_per_material_limit": settings.pro_quizzes_per_material if tier == "pro" else settings.free_quizzes_per_material,
        "can_use_chat": tier == "pro",
    }


def update_subscription_from_stripe(
    user_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: Optional[str],
    status: str,
    trial_end: Optional[datetime],
    current_period_start: Optional[datetime],
    current_period_end: Optional[datetime],
    supabase: Client,
) -> dict:
    """Update subscription from Stripe webhook data."""
    # Find existing subscription
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("user_id", user_id)
        .single()
        .execute()
    )

    update_data = {
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "status": status,
        "trial_start": datetime.now(timezone.utc).isoformat() if status == "trialing" and (not result.data or not result.data.get("trial_start")) else (result.data.get("trial_start") if result.data else None),
        "trial_end": trial_end.isoformat() if trial_end else None,
        "current_period_start": current_period_start.isoformat() if current_period_start else None,
        "current_period_end": current_period_end.isoformat() if current_period_end else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if result.data:
        # Update existing
        update_result = (
            supabase.table("subscriptions")
            .update(update_data)
            .eq("id", result.data["id"])
            .execute()
        )
        return update_result.data[0]
    else:
        # Create new with stripe data
        now = datetime.now(timezone.utc)
        update_data.update({
            "user_id": user_id,
            "uploads_this_week": 0,
            "week_reset_at": (now + timedelta(days=7)).isoformat(),
        })
        create_result = (
            supabase.table("subscriptions")
            .insert(update_data)
            .execute()
        )
        return create_result.data[0]


def cancel_subscription(user_id: UUID, supabase: Client) -> dict:
    """Cancel subscription and downgrade to free."""
    subscription = get_or_create_subscription(user_id, supabase)

    result = (
        supabase.table("subscriptions")
        .update({
            "status": "free",
            "stripe_subscription_id": None,
            "trial_end": None,
            "current_period_start": None,
            "current_period_end": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", subscription["id"])
        .execute()
    )

    return result.data[0]
