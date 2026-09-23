from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 700

    # Database
    database_url: str

    # WordPress
    wp_site_url: str
    wp_api_secret: str

    # JWT
    jwt_secret: str
    jwt_expiry_minutes: int = 60

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_expert: str = ""
    stripe_price_team: str = ""

    # PayPal
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_plan_pro: str = ""
    paypal_plan_expert: str = ""
    paypal_plan_team: str = ""
    paypal_mode: str = "live"

    # Storage
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "mi-assist-uploads"
    r2_endpoint: str = ""

    # App
    cors_origins: List[str] = ["https://yourdomain.com"]
    max_upload_mb: int = 20
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Plan daily message limits
PLAN_LIMITS = {
    "free":    20,
    "pro":     200,
    "expert":  999999,
    "team":    999999,
}

# Plans that allow document uploads (PDF, DOCX, XLSX, CSV, TXT, MD)
UPLOAD_PLANS = {"expert", "team"}

# Plans that allow image analysis
IMAGE_PLANS = {"pro", "expert", "team"}