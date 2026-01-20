from datetime import datetime
from enum import Enum
from typing import Literal, Optional

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
