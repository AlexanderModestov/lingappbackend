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

        sub_status = "trialing" if stripe_sub.status == "trialing" else "active"

        update_subscription_from_stripe(
            user_id=user_id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            status=sub_status,
            trial_end=trial_end,
            current_period_start=period_start,
            current_period_end=period_end,
            supabase=supabase,
        )

        logger.info(f"Subscription created for user {user_id}: {sub_status}")


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
    sub_status = status_map.get(stripe_status, "free")

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
        status=sub_status,
        trial_end=trial_end,
        current_period_start=period_start,
        current_period_end=period_end,
        supabase=supabase,
    )

    logger.info(f"Subscription updated for user {user_id}: {sub_status}")


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
