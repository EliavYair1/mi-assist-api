from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import logging
import stripe
import httpx

from app.database import get_db
from app.models import User
from app.auth import get_current_user
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()
stripe.api_key = settings.stripe_secret_key

# Map Stripe Price IDs → plan names
STRIPE_PRICE_TO_PLAN = {
    settings.stripe_price_pro:      "pro",
    settings.stripe_price_pro_plus: "pro_plus",
    settings.stripe_price_team:     "team",
}

# Map PayPal Plan IDs → plan names
PAYPAL_PLAN_TO_PLAN = {
    settings.paypal_plan_pro:      "pro",
    settings.paypal_plan_pro_plus: "pro_plus",
    settings.paypal_plan_team:     "team",
}


class SubscribeRequest(BaseModel):
    plan: str          # "pro" | "pro_plus" | "team"
    provider: str      # "stripe" | "paypal"


@router.post("/subscribe")
async def create_subscription(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a Stripe Checkout URL or PayPal approval URL.
    The frontend redirects the user there.
    """
    if body.provider == "stripe":
        return await _stripe_checkout(current_user, body.plan)
    elif body.provider == "paypal":
        return await _paypal_create_subscription(current_user, body.plan)
    else:
        raise HTTPException(status_code=400, detail="provider must be 'stripe' or 'paypal'")


@router.get("/portal")
async def billing_portal(current_user: User = Depends(get_current_user)):
    """Redirect to Stripe customer portal for plan management / cancellation."""
    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe account found. Use the PayPal portal instead.")
    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=f"{settings.wp_site_url}/mi-assist/",
    )
    return {"url": session.url}


# ── WEBHOOK (Stripe + PayPal share one endpoint) ──

@router.post("/webhook")
async def billing_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    provider = request.headers.get("X-Provider", "stripe")
    body = await request.body()

    if provider == "paypal":
        data = await request.json()
        await _handle_paypal_event(data, db)
        return {"status": "ok"}

    # Default: Stripe
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(body, sig, settings.stripe_webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    await _handle_stripe_event(event, db)
    return {"status": "ok"}


# ── STRIPE HANDLERS ──

async def _stripe_checkout(user: User, plan: str) -> dict:
    price_id = {
        "pro":      settings.stripe_price_pro,
        "pro_plus": settings.stripe_price_pro_plus,
        "team":     settings.stripe_price_team,
    }.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    # Create or retrieve Stripe customer
    customer_id = user.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(email=user.email, metadata={"mi_user_id": user.id})
        customer_id = customer.id

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{settings.wp_site_url}/mi-assist/?upgraded=1",
        cancel_url=f"{settings.wp_site_url}/mi-assist/?upgrade_canceled=1",
        metadata={"mi_user_id": user.id, "plan": plan},
    )
    return {"checkout_url": session.url, "provider": "stripe"}


async def _handle_stripe_event(event: dict, db: AsyncSession):
    event_type = event["type"]
    obj = event["data"]["object"]
    logger.info(f"Stripe event: {event_type}")

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = obj["customer"]
        status = obj["status"]               # active / past_due / canceled / trialing
        price_id = obj["items"]["data"][0]["price"]["id"]
        plan = STRIPE_PRICE_TO_PLAN.get(price_id, "free")

        await _update_user_by_stripe_customer(db, customer_id, plan, status, obj["id"])

    elif event_type == "customer.subscription.deleted":
        customer_id = obj["customer"]
        await _update_user_by_stripe_customer(db, customer_id, "free", "canceled", None)

    elif event_type == "invoice.payment_failed":
        customer_id = obj["customer"]
        await _update_user_by_stripe_customer(db, customer_id, None, "past_due", None)

    elif event_type == "checkout.session.completed":
        # Link the Stripe customer ID to our user
        mi_user_id = obj.get("metadata", {}).get("mi_user_id")
        customer_id = obj.get("customer")
        if mi_user_id and customer_id:
            result = await db.execute(select(User).where(User.id == mi_user_id))
            user = result.scalar_one_or_none()
            if user:
                user.stripe_customer_id = customer_id
                user.billing_provider = "stripe"
                await db.commit()


async def _update_user_by_stripe_customer(
    db: AsyncSession,
    customer_id: str,
    plan: str | None,
    status: str,
    subscription_id: str | None,
):
    result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"Stripe customer {customer_id} not found in DB")
        return
    if plan is not None:
        user.plan = plan
    user.plan_status = status
    await db.commit()
    logger.info(f"User {user.id} plan={user.plan} status={user.plan_status}")


# ── PAYPAL HANDLERS ──

async def _paypal_create_subscription(user: User, plan: str) -> dict:
    plan_id = {
        "pro":      settings.paypal_plan_pro,
        "pro_plus": settings.paypal_plan_pro_plus,
        "team":     settings.paypal_plan_team,
    }.get(plan)
    if not plan_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    token = await _get_paypal_token()
    base = "https://api-m.sandbox.paypal.com" if settings.paypal_mode == "sandbox" else "https://api-m.paypal.com"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{base}/v1/billing/subscriptions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "plan_id": plan_id,
                "subscriber": {"email_address": user.email},
                "application_context": {
                    "return_url": f"{settings.wp_site_url}/mi-assist/?upgraded=1",
                    "cancel_url": f"{settings.wp_site_url}/mi-assist/?upgrade_canceled=1",
                },
                "custom_id": user.id,
            },
        )
    data = r.json()
    approve_url = next((l["href"] for l in data.get("links", []) if l["rel"] == "approve"), None)
    if not approve_url:
        raise HTTPException(status_code=502, detail="PayPal error: no approval URL")
    return {"checkout_url": approve_url, "provider": "paypal"}


async def _handle_paypal_event(data: dict, db: AsyncSession):
    event_type = data.get("event_type", "")
    resource = data.get("resource", {})
    logger.info(f"PayPal event: {event_type}")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        plan_id = resource.get("plan_id", "")
        plan = PAYPAL_PLAN_TO_PLAN.get(plan_id, "free")
        mi_user_id = resource.get("custom_id")
        sub_id = resource.get("id")
        if mi_user_id:
            await _update_user_by_id(db, mi_user_id, plan, "active", sub_id)

    elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        sub_id = resource.get("id")
        await _update_user_by_paypal_sub(db, sub_id, "free", "canceled")

    elif event_type == "PAYMENT.SALE.DENIED":
        sub_id = resource.get("billing_agreement_id")
        if sub_id:
            await _update_user_by_paypal_sub(db, sub_id, None, "past_due")


async def _update_user_by_id(db: AsyncSession, user_id: str, plan: str, status: str, sub_id: str | None):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.plan = plan
        user.plan_status = status
        user.billing_provider = "paypal"
        if sub_id:
            user.paypal_subscription_id = sub_id
        await db.commit()


async def _update_user_by_paypal_sub(db: AsyncSession, sub_id: str, plan: str | None, status: str):
    result = await db.execute(select(User).where(User.paypal_subscription_id == sub_id))
    user = result.scalar_one_or_none()
    if user:
        if plan is not None:
            user.plan = plan
        user.plan_status = status
        await db.commit()


async def _get_paypal_token() -> str:
    base = "https://api-m.sandbox.paypal.com" if settings.paypal_mode == "sandbox" else "https://api-m.paypal.com"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{base}/v1/oauth2/token",
            auth=(settings.paypal_client_id, settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
        )
    return r.json()["access_token"]
