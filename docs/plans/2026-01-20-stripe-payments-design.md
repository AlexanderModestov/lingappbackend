# Stripe Payments Module Design

## Overview

Payment integration for LinguaMind using Stripe. Implements a freemium model with Free and Pro tiers, including a 7-day trial period for Pro.

## Tier Structure

| Feature | Free Tier | Pro Tier (€20/mo) |
|---------|-----------|-------------------|
| Uploads per week | 1 (configurable) | 10 (configurable) |
| Quizzes per material | 3 (configurable) | 10 (configurable) |
| AI Chat | Disabled | Unlimited |
| Trial | - | 7 days (card required) |

## Business Rules

- **Trial**: 7 days, requires credit card upfront. Auto-charges after trial ends.
- **Cancellation**: Immediate downgrade to Free tier.
- **Payment failure**: Grace period with Stripe retries, then downgrade to Free.
- **Week reset**: Based on user's signup/subscription anniversary (lazy reset on request).
- **Existing users**: Start as Free tier when feature launches.

---

## Configuration

**Environment variables (`app/core/config.py`):**

```python
# Environment toggle
IS_PROD: bool = False  # True = prod Supabase, False = stage Supabase

# Stage Supabase credentials (used when IS_PROD=False)
STAGE_SUPABASE_URL: str
STAGE_SUPABASE_KEY: str
STAGE_SUPABASE_JWT_SECRET: str

# Stripe
STRIPE_SECRET_KEY: str
STRIPE_WEBHOOK_SECRET: str
STRIPE_PRICE_ID: str  # Pro monthly price ID from Stripe dashboard

# Tier limits (all configurable)
FREE_UPLOADS_PER_WEEK: int = 1
FREE_QUIZZES_PER_MATERIAL: int = 3
PRO_UPLOADS_PER_WEEK: int = 10
PRO_QUIZZES_PER_MATERIAL: int = 10
PRO_TRIAL_DAYS: int = 7
```

---

## Database Schema

Complete schema for stage database:

```sql
-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- ============================================
-- SUBSCRIPTIONS (new)
-- ============================================
create table subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade unique not null,

  -- Stripe data
  stripe_customer_id text unique,
  stripe_subscription_id text unique,

  -- Status: 'free', 'trialing', 'active', 'past_due', 'canceled'
  status text not null default 'free',

  -- Trial tracking
  trial_start timestamp with time zone,
  trial_end timestamp with time zone,

  -- Billing period
  current_period_start timestamp with time zone,
  current_period_end timestamp with time zone,

  -- Usage tracking (resets weekly based on created_at anniversary)
  uploads_this_week int not null default 0,
  week_reset_at timestamp with time zone not null default now() + interval '7 days',

  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

-- ============================================
-- MATERIALS
-- ============================================
create table materials (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,

  title text not null,
  source_type text not null check (source_type in ('youtube', 'file')),
  source_url text,
  file_path text,

  -- Extracted content
  text_content text,
  language text,

  -- Processing status
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'failed')),
  error_message text,

  -- Quiz usage tracking (new)
  quiz_count int not null default 0,

  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

-- ============================================
-- FLASHCARDS
-- ============================================
create table flashcards (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,
  material_id uuid references materials(id) on delete cascade not null,

  -- Card content
  term text not null,
  translation text,
  definition text,
  context text,
  grammar_notes text,

  -- SRS (Spaced Repetition System)
  stage int not null default 0,
  next_review timestamp with time zone default now(),
  last_reviewed timestamp with time zone,
  review_count int not null default 0,

  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

-- ============================================
-- QUIZZES
-- ============================================
create table quizzes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,
  material_id uuid references materials(id) on delete cascade not null,

  -- Quiz data
  questions jsonb not null,
  score numeric,
  max_score numeric,

  -- Status
  status text not null default 'created' check (status in ('created', 'completed')),
  completed_at timestamp with time zone,

  created_at timestamp with time zone default now()
);

-- ============================================
-- CHAT MESSAGES
-- ============================================
create table chat_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,
  material_id uuid references materials(id) on delete cascade not null,

  role text not null check (role in ('user', 'assistant')),
  content text not null,

  created_at timestamp with time zone default now()
);

-- ============================================
-- INDEXES
-- ============================================
create index idx_subscriptions_user_id on subscriptions(user_id);
create index idx_subscriptions_stripe_customer_id on subscriptions(stripe_customer_id);
create index idx_materials_user_id on materials(user_id);
create index idx_flashcards_user_id on flashcards(user_id);
create index idx_flashcards_material_id on flashcards(material_id);
create index idx_flashcards_next_review on flashcards(next_review);
create index idx_quizzes_user_id on quizzes(user_id);
create index idx_quizzes_material_id on quizzes(material_id);
create index idx_chat_messages_material_id on chat_messages(material_id);

-- ============================================
-- ROW LEVEL SECURITY
-- ============================================
alter table subscriptions enable row level security;
alter table materials enable row level security;
alter table flashcards enable row level security;
alter table quizzes enable row level security;
alter table chat_messages enable row level security;

-- Subscriptions: users can only view their own
create policy "Users can view own subscription"
  on subscriptions for select
  using (auth.uid() = user_id);

-- Materials: users can CRUD their own
create policy "Users can view own materials"
  on materials for select using (auth.uid() = user_id);
create policy "Users can insert own materials"
  on materials for insert with check (auth.uid() = user_id);
create policy "Users can update own materials"
  on materials for update using (auth.uid() = user_id);
create policy "Users can delete own materials"
  on materials for delete using (auth.uid() = user_id);

-- Flashcards: users can CRUD their own
create policy "Users can view own flashcards"
  on flashcards for select using (auth.uid() = user_id);
create policy "Users can insert own flashcards"
  on flashcards for insert with check (auth.uid() = user_id);
create policy "Users can update own flashcards"
  on flashcards for update using (auth.uid() = user_id);
create policy "Users can delete own flashcards"
  on flashcards for delete using (auth.uid() = user_id);

-- Quizzes: users can CRUD their own
create policy "Users can view own quizzes"
  on quizzes for select using (auth.uid() = user_id);
create policy "Users can insert own quizzes"
  on quizzes for insert with check (auth.uid() = user_id);
create policy "Users can update own quizzes"
  on quizzes for update using (auth.uid() = user_id);
create policy "Users can delete own quizzes"
  on quizzes for delete using (auth.uid() = user_id);

-- Chat messages: users can CRUD their own
create policy "Users can view own chat messages"
  on chat_messages for select using (auth.uid() = user_id);
create policy "Users can insert own chat messages"
  on chat_messages for insert with check (auth.uid() = user_id);
create policy "Users can delete own chat messages"
  on chat_messages for delete using (auth.uid() = user_id);
```

---

## API Endpoints

**New router: `app/routers/payments.py`**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/payments/create-checkout-session` | Creates Stripe Checkout session with 7-day trial, returns checkout URL |
| POST | `/api/v1/payments/webhook` | Receives Stripe webhook events (no auth, verified by signature) |
| GET | `/api/v1/payments/subscription` | Returns current user's subscription status and usage |
| POST | `/api/v1/payments/cancel` | Cancels the user's subscription immediately |

### Webhook Events

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Create/update subscription, set status to 'trialing' |
| `customer.subscription.updated` | Update status ('active', 'past_due'), update period dates |
| `customer.subscription.deleted` | Set status to 'free', clear Stripe IDs |
| `invoice.payment_succeeded` | Confirm 'active' status, update period |
| `invoice.payment_failed` | Set status to 'past_due' (Stripe will retry) |

### Checkout Flow

1. User clicks "Start Pro Trial" in frontend
2. Backend creates Stripe customer (if not exists)
3. Backend creates Checkout Session with:
   - `mode: 'subscription'`
   - `subscription_data.trial_period_days: 7`
   - `success_url` and `cancel_url`
4. Return session URL → frontend redirects to Stripe
5. User completes checkout → Stripe sends webhook → we update DB

---

## Limit Enforcement

**New service: `app/services/subscription.py`**

```python
get_user_subscription(user_id) → Subscription
  # Returns subscription with current status and usage

get_user_tier(user_id) → 'free' | 'pro'
  # Returns 'pro' if status in ('trialing', 'active', 'past_due')
  # Returns 'free' otherwise

check_upload_limit(user_id) → bool
  # Resets uploads_this_week if week_reset_at has passed
  # Returns True if user can upload, False if limit reached

increment_upload_count(user_id)
  # Called after successful material upload

check_quiz_limit(user_id, material_id) → bool
  # Checks material.quiz_count against tier limit

increment_quiz_count(material_id)
  # Called after quiz creation

check_chat_access(user_id) → bool
  # Returns True only if tier is 'pro'
```

### Enforcement Points

| Endpoint | Check |
|----------|-------|
| `POST /materials` | `check_upload_limit()` before processing |
| `POST /quizzes` | `check_quiz_limit()` before generation |
| `POST /chat` | `check_chat_access()` before responding |

### Limit Exceeded Response

```json
HTTP 403 Forbidden
{
  "detail": "Upload limit reached",
  "code": "UPLOAD_LIMIT_REACHED",
  "limit": 1,
  "tier": "free",
  "upgrade_url": "/payments/create-checkout-session"
}
```

### Weekly Reset Logic

- On each upload check, compare `now()` with `week_reset_at`
- If passed: reset `uploads_this_week = 0`, set `week_reset_at = now() + 7 days`
- Lazy reset avoids needing a cron job

---

## File Structure

**New files:**

```
app/
├── routers/
│   └── payments.py          # Checkout, webhook, subscription endpoints
├── services/
│   └── subscription.py      # Tier checks, usage tracking, limit enforcement
├── models/
│   └── subscription.py      # Pydantic models for subscription data
```

**Modified files:**

```
app/core/config.py
  + IS_PROD, STAGE_SUPABASE_*, STRIPE_*, tier limit settings

app/core/security.py
  ~ get_supabase_client() switches based on IS_PROD

app/routers/materials.py
  + Import subscription service
  + Add upload limit check before creating material
  + Increment upload count after success

app/routers/quizzes.py
  + Add quiz limit check before generation
  + Increment quiz count on material after success

app/routers/chat.py
  + Add chat access check (block free users)

app/main.py
  + Include payments router
```

### Pydantic Models

**`app/models/subscription.py`:**

```python
class SubscriptionStatus(str, Enum):
    FREE = "free"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"

class SubscriptionResponse(BaseModel):
    status: SubscriptionStatus
    tier: Literal["free", "pro"]
    trial_end: datetime | None
    current_period_end: datetime | None
    uploads_used: int
    uploads_limit: int
    can_use_chat: bool
```

---

## Error Handling & Edge Cases

### Webhook Security

- Verify Stripe signature using `STRIPE_WEBHOOK_SECRET`
- Reject requests with invalid/missing signatures (401)
- Log all webhook events for debugging

### Idempotency

- Webhooks can be sent multiple times by Stripe
- All webhook handlers check current state before updating
- Example: only set `status = 'active'` if not already active

### Edge Cases

| Scenario | Handling |
|----------|----------|
| User starts trial, never completes checkout | No subscription created (webhook never fires) |
| Webhook arrives before checkout redirect returns | Frontend polls `/subscription` endpoint until status updates |
| User has past_due, then payment succeeds | Webhook updates status back to 'active' |
| User cancels during trial | Immediate downgrade to 'free', clear trial dates |
| Stripe customer already exists | Reuse existing `stripe_customer_id` |
| User deletes account | Cascade delete removes subscription row; optionally cancel Stripe subscription via API |

### Initialization for Existing Users

- On first request to any protected endpoint, if no subscription row exists, create one with `status = 'free'`
- Lazy creation avoids migration script for existing users

---

## Testing

### Stripe Test Mode

- Use Stripe test API keys in stage environment
- Test card numbers: `4242424242424242` (success), `4000000000000341` (decline)
- Stripe CLI for local webhook testing: `stripe listen --forward-to localhost:8000/api/v1/payments/webhook`

### Unit Tests

```
tests/
├── test_subscription_service.py
│   - test_get_user_tier_free
│   - test_get_user_tier_trialing
│   - test_get_user_tier_active
│   - test_check_upload_limit_within_limit
│   - test_check_upload_limit_exceeded
│   - test_weekly_reset_logic
│   - test_check_quiz_limit
│   - test_check_chat_access_free_blocked
│   - test_check_chat_access_pro_allowed
│
├── test_payments_router.py
│   - test_create_checkout_session
│   - test_webhook_checkout_completed
│   - test_webhook_subscription_canceled
│   - test_webhook_invalid_signature
│   - test_get_subscription_status
│   - test_cancel_subscription
│
├── test_limits_integration.py
│   - test_material_upload_blocked_at_limit
│   - test_quiz_creation_blocked_at_limit
│   - test_chat_blocked_for_free_user
```

### Manual Testing Checklist

1. Sign up → verify free tier limits work
2. Start trial → verify checkout redirect
3. Complete checkout → verify trialing status
4. Wait for trial end (or use Stripe test clock) → verify charge
5. Cancel subscription → verify immediate downgrade
6. Test payment failure → verify past_due then recovery
