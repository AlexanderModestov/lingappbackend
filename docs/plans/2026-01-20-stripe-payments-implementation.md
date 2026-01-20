# Stripe Payments Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement Stripe payment integration with Free/Pro tiers, 7-day trial, and usage limits.

**Architecture:** Webhook-driven subscription state management. Stripe Checkout for payment UI. Lazy subscription initialization and weekly reset. Limit enforcement via dependency injection in existing routers.

**Tech Stack:** FastAPI, Stripe Python SDK, Supabase, Pydantic

---

## Task 1: Add Stripe Dependency

**Files:**
- Modify: `requirements.txt:12`

**Step 1: Add stripe package**

Add to `requirements.txt`:
```
stripe>=7.0.0,<8.0.0
```

**Step 2: Commit**

```bash
git add requirements.txt
git commit -m "feat: add stripe dependency"
```

---

## Task 2: Update Configuration Settings

**Files:**
- Modify: `app/core/config.py`

**Step 1: Add new settings to Settings class**

Replace entire `app/core/config.py`:

```python
import json
from functools import lru_cache
from typing import List, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Environment toggle
    is_prod: bool = False

    # Production Supabase
    supabase_url: str
    supabase_key: str
    supabase_jwt_secret: str

    # Stage Supabase (used when is_prod=False)
    stage_supabase_url: str = ""
    stage_supabase_key: str = ""
    stage_supabase_jwt_secret: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""  # Pro monthly price ID from Stripe dashboard

    # Tier limits (configurable)
    free_uploads_per_week: int = 1
    free_quizzes_per_material: int = 3
    pro_uploads_per_week: int = 10
    pro_quizzes_per_material: int = 10
    pro_trial_days: int = 7

    # OpenAI
    openai_api_key: str

    # Application
    debug: bool = False
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # API Settings
    api_v1_prefix: str = "/api/v1"

    def get_active_supabase_url(self) -> str:
        """Get the active Supabase URL based on environment."""
        if self.is_prod:
            return self.supabase_url
        return self.stage_supabase_url or self.supabase_url

    def get_active_supabase_key(self) -> str:
        """Get the active Supabase key based on environment."""
        if self.is_prod:
            return self.supabase_key
        return self.stage_supabase_key or self.supabase_key

    def get_active_supabase_jwt_secret(self) -> str:
        """Get the active Supabase JWT secret based on environment."""
        if self.is_prod:
            return self.supabase_jwt_secret
        return self.stage_supabase_jwt_secret or self.supabase_jwt_secret


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
```

**Step 2: Commit**

```bash
git add app/core/config.py
git commit -m "feat: add stripe and tier limit settings with env switching"
```

---

## Task 3: Update Security Module for Environment Switching

**Files:**
- Modify: `app/core/security.py`

**Step 1: Update get_supabase_client and verify_token to use active credentials**

Replace entire `app/core/security.py`:

```python
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
```

**Step 2: Commit**

```bash
git add app/core/security.py
git commit -m "feat: use environment-based supabase credentials"
```

---

## Task 4: Create Subscription Models

**Files:**
- Create: `app/models/subscription.py`

**Step 1: Create subscription Pydantic models**

Create `app/models/subscription.py`:

```python
from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class SubscriptionStatus(str, Enum):
    FREE = "free"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class SubscriptionResponse(BaseModel):
    """Response model for subscription status endpoint."""

    status: SubscriptionStatus
    tier: Literal["free", "pro"]
    trial_end: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    uploads_used: int
    uploads_limit: int
    quizzes_per_material_limit: int
    can_use_chat: bool


class CheckoutSessionResponse(BaseModel):
    """Response model for checkout session creation."""

    checkout_url: str
    session_id: str


class LimitExceededError(BaseModel):
    """Response model for limit exceeded errors."""

    detail: str
    code: str
    limit: int
    tier: str
    upgrade_url: str
```

**Step 2: Commit**

```bash
git add app/models/subscription.py
git commit -m "feat: add subscription pydantic models"
```

---

## Task 5: Create Subscription Service

**Files:**
- Create: `app/services/subscription.py`

**Step 1: Create the subscription service with all limit checking functions**

Create `app/services/subscription.py`:

```python
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
        "trial_start": datetime.now(timezone.utc).isoformat() if status == "trialing" and not result.data.get("trial_start") else result.data.get("trial_start") if result.data else None,
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
```

**Step 2: Commit**

```bash
git add app/services/subscription.py
git commit -m "feat: add subscription service with limit checking"
```

---

## Task 6: Create Payments Router

**Files:**
- Create: `app/routers/payments.py`

**Step 1: Create payments router with Stripe integration**

Create `app/routers/payments.py`:

```python
import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from supabase import Client

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.models.subscription import CheckoutSessionResponse, SubscriptionResponse
from app.services.subscription import (
    cancel_subscription,
    get_or_create_subscription,
    get_subscription_response,
    update_subscription_from_stripe,
)

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> CheckoutSessionResponse:
    """Create a Stripe Checkout session for Pro subscription with trial."""
    stripe.api_key = settings.stripe_secret_key

    # Get or create subscription to check for existing Stripe customer
    subscription = get_or_create_subscription(current_user.id, supabase)

    # Check if user already has an active subscription
    if subscription.get("status") in ("trialing", "active", "past_due"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active subscription",
        )

    # Get or create Stripe customer
    customer_id = subscription.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=current_user.email,
            metadata={"user_id": str(current_user.id)},
        )
        customer_id = customer.id

        # Save customer ID
        supabase.table("subscriptions").update({
            "stripe_customer_id": customer_id,
        }).eq("id", subscription["id"]).execute()

    # Create checkout session
    checkout_session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{
            "price": settings.stripe_price_id,
            "quantity": 1,
        }],
        subscription_data={
            "trial_period_days": settings.pro_trial_days,
            "metadata": {"user_id": str(current_user.id)},
        },
        success_url=f"{settings.cors_origins[0]}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.cors_origins[0]}/subscription/cancel",
        metadata={"user_id": str(current_user.id)},
    )

    return CheckoutSessionResponse(
        checkout_url=checkout_session.url,
        session_id=checkout_session.id,
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
):
    """Handle Stripe webhook events."""
    stripe.api_key = settings.stripe_secret_key

    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Stripe signature",
        )

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload,
            stripe_signature,
            settings.stripe_webhook_secret,
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        )
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    logger.info(f"Stripe webhook received: {event.type}")

    # Handle the event
    if event.type == "checkout.session.completed":
        session = event.data.object
        await handle_checkout_completed(session, supabase)

    elif event.type == "customer.subscription.updated":
        subscription = event.data.object
        await handle_subscription_updated(subscription, supabase)

    elif event.type == "customer.subscription.deleted":
        subscription = event.data.object
        await handle_subscription_deleted(subscription, supabase)

    elif event.type == "invoice.payment_succeeded":
        invoice = event.data.object
        await handle_payment_succeeded(invoice, supabase)

    elif event.type == "invoice.payment_failed":
        invoice = event.data.object
        await handle_payment_failed(invoice, supabase)

    return {"status": "ok"}


async def handle_checkout_completed(session: dict, supabase: Client):
    """Handle successful checkout completion."""
    user_id = session.get("metadata", {}).get("user_id")
    if not user_id:
        logger.error("No user_id in checkout session metadata")
        return

    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if subscription_id:
        # Get subscription details from Stripe
        stripe_sub = stripe.Subscription.retrieve(subscription_id)

        trial_end = None
        if stripe_sub.trial_end:
            trial_end = datetime.fromtimestamp(stripe_sub.trial_end, tz=timezone.utc)

        period_start = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)
        period_end = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)

        status = "trialing" if stripe_sub.status == "trialing" else "active"

        update_subscription_from_stripe(
            user_id=user_id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            status=status,
            trial_end=trial_end,
            current_period_start=period_start,
            current_period_end=period_end,
            supabase=supabase,
        )

        logger.info(f"Subscription created for user {user_id}: {status}")


async def handle_subscription_updated(subscription: dict, supabase: Client):
    """Handle subscription updates (status changes, renewals)."""
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")
    stripe_status = subscription.get("status")

    # Map Stripe status to our status
    status_map = {
        "trialing": "trialing",
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "past_due",
    }
    status = status_map.get(stripe_status, "free")

    # Find user by stripe_customer_id
    result = (
        supabase.table("subscriptions")
        .select("user_id")
        .eq("stripe_customer_id", customer_id)
        .single()
        .execute()
    )

    if not result.data:
        logger.error(f"No subscription found for customer {customer_id}")
        return

    user_id = result.data["user_id"]

    trial_end = None
    if subscription.get("trial_end"):
        trial_end = datetime.fromtimestamp(subscription["trial_end"], tz=timezone.utc)

    period_start = datetime.fromtimestamp(subscription["current_period_start"], tz=timezone.utc)
    period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc)

    update_subscription_from_stripe(
        user_id=user_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        status=status,
        trial_end=trial_end,
        current_period_start=period_start,
        current_period_end=period_end,
        supabase=supabase,
    )

    logger.info(f"Subscription updated for user {user_id}: {status}")


async def handle_subscription_deleted(subscription: dict, supabase: Client):
    """Handle subscription cancellation/deletion."""
    customer_id = subscription.get("customer")

    # Find user by stripe_customer_id
    result = (
        supabase.table("subscriptions")
        .select("user_id")
        .eq("stripe_customer_id", customer_id)
        .single()
        .execute()
    )

    if not result.data:
        logger.error(f"No subscription found for customer {customer_id}")
        return

    user_id = result.data["user_id"]

    # Downgrade to free
    supabase.table("subscriptions").update({
        "status": "free",
        "stripe_subscription_id": None,
        "trial_end": None,
        "current_period_start": None,
        "current_period_end": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).execute()

    logger.info(f"Subscription deleted for user {user_id}, downgraded to free")


async def handle_payment_succeeded(invoice: dict, supabase: Client):
    """Handle successful payment."""
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")

    if not subscription_id:
        return  # Not a subscription invoice

    # Find user by stripe_customer_id
    result = (
        supabase.table("subscriptions")
        .select("user_id")
        .eq("stripe_customer_id", customer_id)
        .single()
        .execute()
    )

    if not result.data:
        return

    # Update status to active
    supabase.table("subscriptions").update({
        "status": "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", result.data["user_id"]).execute()

    logger.info(f"Payment succeeded for user {result.data['user_id']}")


async def handle_payment_failed(invoice: dict, supabase: Client):
    """Handle failed payment."""
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")

    if not subscription_id:
        return  # Not a subscription invoice

    # Find user by stripe_customer_id
    result = (
        supabase.table("subscriptions")
        .select("user_id")
        .eq("stripe_customer_id", customer_id)
        .single()
        .execute()
    )

    if not result.data:
        return

    # Set to past_due (Stripe will retry)
    supabase.table("subscriptions").update({
        "status": "past_due",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", result.data["user_id"]).execute()

    logger.info(f"Payment failed for user {result.data['user_id']}, set to past_due")


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription_status(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> SubscriptionResponse:
    """Get current user's subscription status."""
    data = get_subscription_response(current_user.id, supabase, settings)
    return SubscriptionResponse(**data)


@router.post("/cancel", response_model=SubscriptionResponse)
async def cancel_user_subscription(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> SubscriptionResponse:
    """Cancel the user's subscription immediately."""
    stripe.api_key = settings.stripe_secret_key

    subscription = get_or_create_subscription(current_user.id, supabase)

    if subscription.get("status") == "free":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription to cancel",
        )

    # Cancel in Stripe
    stripe_sub_id = subscription.get("stripe_subscription_id")
    if stripe_sub_id:
        try:
            stripe.Subscription.cancel(stripe_sub_id)
        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel Stripe subscription: {e}")

    # Update local status
    cancel_subscription(current_user.id, supabase)

    # Return updated status
    data = get_subscription_response(current_user.id, supabase, settings)
    return SubscriptionResponse(**data)
```

**Step 2: Commit**

```bash
git add app/routers/payments.py
git commit -m "feat: add payments router with stripe checkout and webhooks"
```

---

## Task 7: Register Payments Router in Main App

**Files:**
- Modify: `app/main.py`

**Step 1: Add payments router to main app**

Replace entire `app/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, cards, chat, materials, payments, quizzes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LinguaMind API",
        description="Language learning through content consumption",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "version": "0.1.0"}

    # Include routers
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(materials.router, prefix=settings.api_v1_prefix)
    app.include_router(cards.router, prefix=settings.api_v1_prefix)
    app.include_router(quizzes.router, prefix=settings.api_v1_prefix)
    app.include_router(chat.router, prefix=settings.api_v1_prefix)
    app.include_router(payments.router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
```

**Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat: register payments router"
```

---

## Task 8: Add Upload Limit Check to Materials Router

**Files:**
- Modify: `app/routers/materials.py`

**Step 1: Add limit checking to upload endpoints**

Add these imports at the top of `app/routers/materials.py` (after existing imports):

```python
from app.core.config import Settings, get_settings
from app.services.subscription import check_upload_limit, increment_upload_count
```

**Step 2: Add limit check to `upload_youtube_material` function**

Replace the `upload_youtube_material` function:

```python
@router.post("/upload/youtube", response_model=MaterialResponse)
async def upload_youtube_material(
    data: MaterialCreateYouTube,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> MaterialResponse:
    """Create a new material from a YouTube URL."""
    # Check upload limit
    can_upload, current, limit = check_upload_limit(current_user.id, supabase, settings)
    if not can_upload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Upload limit reached",
                "code": "UPLOAD_LIMIT_REACHED",
                "limit": limit,
                "current": current,
                "upgrade_url": "/api/v1/payments/create-checkout-session",
            },
        )

    result = (
        supabase.table("materials")
        .insert(
            {
                "user_id": str(current_user.id),
                "title": data.title,
                "source_type": SourceType.YOUTUBE,
                "source_url": str(data.url),
                "processing_status": ProcessingStatus.PENDING,
            }
        )
        .execute()
    )

    # Increment upload count after successful creation
    increment_upload_count(current_user.id, supabase)

    return MaterialResponse(**result.data[0])
```

**Step 3: Add limit check to `upload_file_material` function**

Replace the `upload_file_material` function:

```python
@router.post("/upload/file", response_model=MaterialResponse)
async def upload_file_material(
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> MaterialResponse:
    """Upload a file (PDF, DOCX) and create a new material."""
    # Check upload limit
    can_upload, current, limit = check_upload_limit(current_user.id, supabase, settings)
    if not can_upload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Upload limit reached",
                "code": "UPLOAD_LIMIT_REACHED",
                "limit": limit,
                "current": current,
                "upgrade_url": "/api/v1/payments/create-checkout-session",
            },
        )

    # Validate file type
    file_ext = Path(file.filename or "").suffix.lower()
    if not is_supported_file(file.filename or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file_ext}",
        )

    # Generate unique file path
    file_id = str(uuid.uuid4())
    storage_path = f"{current_user.id}/{file_id}{file_ext}"

    # Upload to Supabase Storage
    content = await file.read()
    supabase.storage.from_("uploads").upload(
        storage_path,
        content,
        file_options={"content-type": file.content_type or "application/octet-stream"},
    )

    # Create material record
    result = (
        supabase.table("materials")
        .insert(
            {
                "user_id": str(current_user.id),
                "title": title,
                "source_type": SourceType.FILE,
                "file_path": storage_path,
                "processing_status": ProcessingStatus.PENDING,
            }
        )
        .execute()
    )

    # Increment upload count after successful creation
    increment_upload_count(current_user.id, supabase)

    return MaterialResponse(**result.data[0])
```

**Step 4: Commit**

```bash
git add app/routers/materials.py
git commit -m "feat: add upload limit checking to materials router"
```

---

## Task 9: Add Quiz Limit Check to Quizzes Router

**Files:**
- Modify: `app/routers/quizzes.py`

**Step 1: Add imports**

Add these imports at the top of `app/routers/quizzes.py` (after existing imports):

```python
from app.core.config import Settings, get_settings
from app.services.subscription import check_quiz_limit, increment_quiz_count
```

**Step 2: Update `create_quiz` function**

Replace the `create_quiz` function:

```python
@router.post("", response_model=QuizResponse)
async def create_quiz(
    data: QuizCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
    settings: Settings = Depends(get_settings),
) -> QuizResponse:
    """Generate a new quiz for a material."""
    # Check quiz limit
    can_create, current, limit = check_quiz_limit(
        current_user.id, str(data.material_id), supabase, settings
    )
    if not can_create:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Quiz limit reached for this material",
                "code": "QUIZ_LIMIT_REACHED",
                "limit": limit,
                "current": current,
                "upgrade_url": "/api/v1/payments/create-checkout-session",
            },
        )

    # Get material and verify ownership
    material_result = (
        supabase.table("materials")
        .select("id, processed_text, processing_status")
        .eq("id", str(data.material_id))
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not material_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    material = material_result.data

    if material["processing_status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material must be processed before creating a quiz",
        )

    if not material.get("processed_text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material has no processed text",
        )

    # Generate quiz questions
    try:
        questions = generate_quiz(material["processed_text"], data.num_questions)
    except Exception as e:
        logger.error(f"Failed to generate quiz: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate quiz questions",
        )

    # Save quiz to database
    result = (
        supabase.table("quizzes")
        .insert({
            "material_id": str(data.material_id),
            "user_id": str(current_user.id),
            "questions": questions,
            "total_questions": len(questions),
        })
        .execute()
    )

    # Increment quiz count for material
    increment_quiz_count(str(data.material_id), supabase)

    quiz_data = result.data[0]
    return QuizResponse(
        id=quiz_data["id"],
        material_id=quiz_data["material_id"],
        questions=quiz_data["questions"],
        score=quiz_data.get("score"),
        total_questions=quiz_data["total_questions"],
        completed_at=quiz_data.get("completed_at"),
        created_at=quiz_data["created_at"],
    )
```

**Step 3: Commit**

```bash
git add app/routers/quizzes.py
git commit -m "feat: add quiz limit checking to quizzes router"
```

---

## Task 10: Add Chat Access Check to Chat Router

**Files:**
- Modify: `app/routers/chat.py`

**Step 1: Add imports**

Add this import at the top of `app/routers/chat.py` (after existing imports):

```python
from app.services.subscription import check_chat_access
```

**Step 2: Update `send_message` function**

Add this check at the beginning of the `send_message` function (after the function signature):

```python
@router.post("/{material_id}", response_model=ChatResponse)
async def send_message(
    material_id: str,
    data: ChatSend,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> ChatResponse:
    """Send a message and get AI response."""
    # Check chat access (Pro only)
    if not check_chat_access(current_user.id, supabase):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Chat is only available for Pro users",
                "code": "CHAT_ACCESS_DENIED",
                "upgrade_url": "/api/v1/payments/create-checkout-session",
            },
        )

    # Get material and verify ownership
    material_result = (
        supabase.table("materials")
        .select("id, title, processed_text, processing_status")
        .eq("id", material_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not material_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    material = material_result.data

    if material["processing_status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material must be processed before chatting",
        )

    if not material.get("processed_text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material has no processed text",
        )

    # Get existing chat history
    history_result = (
        supabase.table("chat_messages")
        .select("role, content")
        .eq("material_id", material_id)
        .eq("user_id", str(current_user.id))
        .order("created_at", desc=False)
        .execute()
    )

    chat_history = history_result.data

    # Save user message
    user_msg_result = (
        supabase.table("chat_messages")
        .insert({
            "material_id": material_id,
            "user_id": str(current_user.id),
            "role": "user",
            "content": data.message,
        })
        .execute()
    )

    user_message = ChatMessage(**user_msg_result.data[0])

    # Generate AI response
    try:
        assistant_content = get_chat_response(
            material_text=material["processed_text"],
            material_title=material["title"],
            chat_history=chat_history,
            user_message=data.message,
        )
    except Exception as e:
        logger.error(f"Failed to generate chat response: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate response",
        )

    # Save assistant message
    assistant_msg_result = (
        supabase.table("chat_messages")
        .insert({
            "material_id": material_id,
            "user_id": str(current_user.id),
            "role": "assistant",
            "content": assistant_content,
        })
        .execute()
    )

    assistant_message = ChatMessage(**assistant_msg_result.data[0])

    return ChatResponse(
        user_message=user_message,
        assistant_message=assistant_message,
    )
```

**Step 3: Commit**

```bash
git add app/routers/chat.py
git commit -m "feat: add chat access check for pro users only"
```

---

## Task 11: Create Example .env File

**Files:**
- Create: `.env.example`

**Step 1: Create example environment file**

Create `.env.example`:

```
# Environment
IS_PROD=false

# Production Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
SUPABASE_JWT_SECRET={"kty":"EC",...}

# Stage Supabase (used when IS_PROD=false)
STAGE_SUPABASE_URL=https://your-stage-project.supabase.co
STAGE_SUPABASE_KEY=your-stage-service-role-key
STAGE_SUPABASE_JWT_SECRET={"kty":"EC",...}

# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...

# Tier limits (optional, defaults shown)
FREE_UPLOADS_PER_WEEK=1
FREE_QUIZZES_PER_MATERIAL=3
PRO_UPLOADS_PER_WEEK=10
PRO_QUIZZES_PER_MATERIAL=10
PRO_TRIAL_DAYS=7

# OpenAI
OPENAI_API_KEY=sk-...

# Application
DEBUG=true
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add example environment file with stripe config"
```

---

## Task 12: Final Integration Commit

**Step 1: Verify all files are committed**

```bash
git status
```

Expected: Clean working tree

**Step 2: Create summary commit if needed**

If there are any uncommitted changes:

```bash
git add -A
git commit -m "feat: complete stripe payments integration

- Add Free/Pro tiers with configurable limits
- Stripe Checkout for subscription with 7-day trial
- Webhook handlers for subscription lifecycle
- Upload limits (1/week free, 10/week pro)
- Quiz limits per material (3 free, 10 pro)
- Chat access for Pro users only
- Environment switching between prod/stage Supabase"
```

---

## Summary

**New Files Created:**
- `app/models/subscription.py` - Pydantic models
- `app/services/subscription.py` - Limit checking logic
- `app/routers/payments.py` - Stripe endpoints
- `.env.example` - Environment template

**Modified Files:**
- `requirements.txt` - Added stripe dependency
- `app/core/config.py` - Added stripe and tier settings
- `app/core/security.py` - Environment-based Supabase
- `app/main.py` - Registered payments router
- `app/routers/materials.py` - Upload limit checks
- `app/routers/quizzes.py` - Quiz limit checks
- `app/routers/chat.py` - Chat access checks

**Database:** Run the SQL schema from `docs/plans/2026-01-20-stripe-payments-design.md` in your stage Supabase project.

**Stripe Setup:**
1. Create a product and price in Stripe dashboard
2. Set up webhook endpoint: `https://your-api.com/api/v1/payments/webhook`
3. Subscribe to events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_succeeded`, `invoice.payment_failed`
