
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# Re-use your existing JWT auth dependency — adjust the import to match your project
# from auth import get_current_user   ← change this line to match your auth module
# For now we import a stub; replace with your real dependency:
try:
    from auth import get_current_user          # your existing JWT dependency
except ImportError:
    async def get_current_user():              # fallback stub so file is importable
        return {"user_id": 0, "email": ""}

router = APIRouter(prefix="/notify", tags=["notifications"])

SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL        = os.getenv("SENDGRID_FROM_EMAIL", "noreply@metrointegrity.com")
FROM_NAME         = "MI Assist"


class NotifyPayload(BaseModel):
    type: str          # "reply" | "quota" | "upload"
    content: str       # The body text / reply text
    email: str         # Recipient email (sent by the JS from MI_ASSIST.email)
    subject: str = ""  # Optional override


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via SendGrid. Returns True on success."""
    if not SENDGRID_API_KEY:
        print("[notify] SENDGRID_API_KEY not set – email skipped")
        return False
    message = Mail(
        from_email=(FROM_EMAIL, FROM_NAME),
        to_emails=to_email,
        subject=subject,
        html_content=html_body,
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"[notify] email sent to {to_email} – status {response.status_code}")
        return response.status_code in (200, 202)
    except Exception as e:
        print(f"[notify] SendGrid error: {e}")
        return False


def _build_html(title: str, body_text: str, cta_label: str = "", cta_url: str = "") -> str:
    cta_block = ""
    if cta_label and cta_url:
        cta_block = f"""
        <a href="{cta_url}"
           style="display:inline-block;margin-top:20px;padding:10px 24px;
                  background:#2271DA;color:#fff;border-radius:8px;
                  text-decoration:none;font-size:14px;font-weight:600;">
          {cta_label}
        </a>"""
    return f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px;">
      <div style="margin-bottom:24px;">
        <span style="font-size:22px;font-weight:700;color:#000D22;">MI</span>
        <span style="font-size:14px;color:#2271DA;font-weight:600;margin-left:4px;">ASSIST</span>
      </div>
      <h2 style="color:#000D22;font-size:18px;margin:0 0 12px;">{title}</h2>
      <div style="background:#f7f9fc;border-radius:10px;padding:16px 20px;
                  color:#374151;font-size:14px;line-height:1.7;white-space:pre-wrap;">{body_text}</div>
      {cta_block}
      <hr style="border:none;border-top:1px solid #eeeeee;margin:28px 0;">
      <p style="color:#8b96a8;font-size:11px;">
        You're receiving this because email notifications are enabled in your MI Assist settings.
        <a href="#" style="color:#8b96a8;">Unsubscribe</a>
      </p>
    </div>
    """


@router.post("/email")
async def send_notification_email(
    payload: NotifyPayload,
    user=Depends(get_current_user),
):
    """
    Called by the JS frontend to send an email notification via SendGrid.
    Types:
      "reply"  – AI response received (Email Updates setting)
      "quota"  – usage limit reached  (Usage Alerts setting)
      "upload" – file processed       (Email Updates setting)
    """
    if not payload.email:
        raise HTTPException(status_code=400, detail="No email address provided")

    if payload.type == "reply":
        subject = payload.subject or "MI Assist replied to your question"
        html    = _build_html(
            title    = "New reply from MI Assist",
            body_text= payload.content,
            cta_label= "Open MI Assist",
            cta_url  = "https://metrointegrity.com",
        )

    elif payload.type == "quota":
        subject = payload.subject or "⚠️ You've reached your MI Assist plan limit"
        html    = _build_html(
            title    = "Plan limit reached",
            body_text= payload.content or
                       "You have used all your questions for this period.\n"
                       "Upgrade your plan to continue using MI Assist.",
            cta_label= "Upgrade Plan",
            cta_url  = "https://metrointegrity.com/pricing",
        )

    elif payload.type == "upload":
        subject = payload.subject or "MI Assist processed your file"
        html    = _build_html(
            title    = "File ready",
            body_text= payload.content,
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown notification type: {payload.type}")

    ok = _send_email(payload.email, subject, html)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to send email")

    return {"status": "sent", "to": payload.email, "type": payload.type}
