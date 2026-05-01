from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 700

    # Database
    database_url: str  # postgresql+asyncpg://user:pass@localhost/mi_assist

    # WordPress
    wp_site_url: str               # https://yourdomain.com
    wp_api_secret: str             # shared secret for nonce validation

    # JWT
    jwt_secret: str
    jwt_expiry_minutes: int = 60

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_pro_plus: str = ""
    stripe_price_team: str = ""

    # PayPal
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_plan_pro: str = ""
    paypal_plan_pro_plus: str = ""
    paypal_plan_team: str = ""
    paypal_mode: str = "live"      # "sandbox" for testing

    # Storage (Cloudflare R2 — compatible with boto3)
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "mi-assist-uploads"
    r2_endpoint: str = ""          # https://<account_id>.r2.cloudflarestorage.com

    # App
    cors_origins: List[str] = ["https://yourdomain.com"]
    daily_limit_free: int = 5
    daily_limit_paid: int = 200
    upload_limit_pro_plus: int = 20   # per month
    max_upload_mb: int = 10
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Plan daily message limits
PLAN_LIMITS = {
    "free":     settings.daily_limit_free,
    "pro":      settings.daily_limit_paid,
    "pro_plus": settings.daily_limit_paid,
    "team":     settings.daily_limit_paid,
}

# Plans that allow file upload
UPLOAD_PLANS = {"pro_plus", "team"}

# Plans that allow image analysis
IMAGE_PLANS = {"pro", "pro_plus", "team"}
