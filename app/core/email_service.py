"""
MR AI RAG - Email Service
Sends OTP emails via Gmail SMTP using Python's built-in smtplib.
"""

import hashlib
import logging
import random
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


# ── OTP Helpers ───────────────────────────────────────────────────────────────

def generate_otp() -> str:
    """Generate a 6-digit OTP string."""
    return str(random.randint(100000, 999999))


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


# ── Database OTP Management ───────────────────────────────────────────────────

def store_otp(db, email: str, otp: str, purpose: str = "register") -> None:
    """Store hashed OTP in the DB, invalidating any previous ones for same email+purpose."""
    from app.core.models import EmailOTP

    # Invalidate old OTPs for this email+purpose
    db.query(EmailOTP).filter(
        EmailOTP.email == email.lower(),
        EmailOTP.purpose == purpose,
        EmailOTP.used == False,
    ).update({"used": True})
    db.commit()

    otp_record = EmailOTP(
        email=email.lower(),
        purpose=purpose,
        otp_hash=hash_otp(otp),
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        used=False,
    )
    db.add(otp_record)
    db.commit()
    logger.info(f"OTP stored for {email} (purpose={purpose})")


def verify_otp(db, email: str, otp: str, purpose: str = "register") -> bool:
    """Verify OTP. Returns True if valid and unexpired. Marks it as used."""
    from app.core.models import EmailOTP

    record = db.query(EmailOTP).filter(
        EmailOTP.email == email.lower(),
        EmailOTP.purpose == purpose,
        EmailOTP.used == False,
        EmailOTP.otp_hash == hash_otp(otp),
    ).order_by(EmailOTP.created_at.desc()).first()

    if not record:
        return False
    if record.expires_at < datetime.utcnow():
        record.used = True
        db.commit()
        return False

    record.used = True
    db.commit()
    logger.info(f"OTP verified for {email} (purpose={purpose})")
    return True


# ── Email Sending ─────────────────────────────────────────────────────────────

def send_otp_email(to_email: str, otp: str, purpose: str = "register", name: str = "") -> bool:
    """
    Send an OTP email via Gmail SMTP.
    Returns True on success, False on failure.
    """
    from app.core.config import settings

    if purpose == "register":
        subject = "🔑 Verify Your MR AI RAG Account"
        action = "complete your registration"
        color = "#6c63ff"
    else:
        subject = "🔐 Reset Your MR AI RAG Password"
        action = "reset your password"
        color = "#00d4aa"

    greeting = f"Hi {name}," if name else "Hello,"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0d0f17;font-family:Inter,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1a1e2e;border-radius:16px;border:1px solid #2a2f47;overflow:hidden">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#6c63ff,#00d4aa);padding:28px 32px;text-align:center">
          <div style="font-size:28px;font-weight:900;color:#fff;letter-spacing:-0.5px">MR AI RAG</div>
          <div style="font-size:13px;color:rgba(255,255,255,.75);margin-top:4px">API Intelligence Platform</div>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:32px 36px">
          <p style="color:#e2e8f0;font-size:15px;margin-bottom:12px">{greeting}</p>
          <p style="color:#94a3b8;font-size:13px;line-height:1.7;margin-bottom:24px">
            You requested to {action}. Use the verification code below:
          </p>
          <!-- OTP Box -->
          <div style="background:#0d0f17;border:2px solid {color};border-radius:12px;padding:24px;text-align:center;margin-bottom:24px">
            <div style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:2px;margin-bottom:10px">Your OTP Code</div>
            <div style="font-size:42px;font-weight:900;letter-spacing:10px;color:{color};font-family:'Courier New',monospace">{otp}</div>
            <div style="color:#64748b;font-size:11px;margin-top:10px">⏱ Valid for 10 minutes</div>
          </div>
          <p style="color:#64748b;font-size:12px;line-height:1.6;margin:0">
            If you didn't request this, you can safely ignore this email.<br>
            Never share this code with anyone.
          </p>
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:16px 36px;border-top:1px solid #2a2f47;text-align:center">
          <p style="color:#475569;font-size:11px;margin:0">© 2024 MR AI RAG Platform</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    text_body = f"{greeting}\n\nYour OTP code: {otp}\nValid for 10 minutes.\n\nIgnore if you didn't request this."

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_USER
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, to_email, msg.as_string())

        logger.info(f"OTP email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email}: {e}")
        return False
