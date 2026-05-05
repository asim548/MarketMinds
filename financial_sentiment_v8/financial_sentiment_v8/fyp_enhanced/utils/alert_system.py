"""
FinancialPulse v4 — Alert System
══════════════════════════════════
Channels:
  • Telegram Bot API
  • Email (SMTP / Gmail)
  • WhatsApp via Twilio WhatsApp API

Configuration (.env):
    TELEGRAM_BOT_TOKEN=xxx
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465
    SMTP_USER=you@gmail.com
    SMTP_PASS=app_password
    TWILIO_ACCOUNT_SID=ACxxx
    TWILIO_AUTH_TOKEN=xxx
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
"""

import os
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

logger = logging.getLogger(__name__)


# ── Message builder ───────────────────────────────────────────────────────────

def build_alert_message(asset_label: str, asset_icon: str,
                         direction: str, score: float,
                         signal: str, article_title: str,
                         threshold: float) -> dict:
    arrow = "🟢" if direction == "bullish" else "🔴"
    ts = datetime.utcnow().strftime("%H:%M UTC, %b %d %Y")

    tg_msg = (
        f"<b>⚡ FinancialPulse Alert</b>\n"
        f"{arrow} <b>{asset_icon} {asset_label}</b> — <b>{direction.upper()}</b> threshold crossed!\n\n"
        f"📊 Sentiment Score: <code>{score:+.3f}</code>\n"
        f"🎯 AI Signal: <b>{signal}</b>\n"
        f"📰 Trigger: {article_title[:120]}\n"
        f"⏰ {ts}\n\n"
        f"<i>Threshold: {threshold:.0%} | FinancialPulse v4</i>"
    )

    whatsapp_msg = (
        f"⚡ *FinancialPulse Alert*\n"
        f"{arrow} *{asset_icon} {asset_label}* — {direction.upper()} threshold crossed!\n\n"
        f"📊 Score: `{score:+.3f}`\n"
        f"🎯 Signal: *{signal}*\n"
        f"📰 {article_title[:100]}\n"
        f"⏰ {ts}"
    )

    email_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0f172a;color:#e2e8f0;padding:24px;border-radius:12px;">
      <h2 style="color:#00ff88;margin:0 0 16px">⚡ FinancialPulse Sentiment Alert</h2>
      <div style="background:#1e293b;padding:16px;border-radius:8px;margin-bottom:16px;">
        <p style="font-size:20px;margin:0">{arrow} <b>{asset_icon} {asset_label}</b></p>
        <p style="color:#94a3b8;margin:4px 0 0">{direction.upper()} signal detected</p>
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:8px;color:#94a3b8;width:140px">Sentiment Score</td>
            <td style="padding:8px;color:#00ff88;font-weight:bold">{score:+.3f}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8">AI Signal</td>
            <td style="padding:8px;color:#f59e0b;font-weight:bold">{signal}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8">Trigger Article</td>
            <td style="padding:8px">{article_title[:150]}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8">Time</td>
            <td style="padding:8px">{ts}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8">Threshold</td>
            <td style="padding:8px">{threshold:.0%}</td></tr>
      </table>
      <p style="color:#64748b;font-size:11px;margin-top:16px;border-top:1px solid #1e293b;padding-top:12px">
        FinancialPulse v4 · AI-Powered Market Sentiment Intelligence
      </p>
    </div>"""

    return {
        "telegram":  tg_msg,
        "whatsapp":  whatsapp_msg,
        "email":     email_html,
        "subject":   f"⚡ {asset_icon} {asset_label} {direction.upper()} Alert — FinancialPulse",
    }


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    if not bot_token or not chat_id:
        logger.warning("[Alert/Telegram] Missing bot_token or chat_id")
        return False
    try:
        url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        ok = resp.status_code == 200
        if not ok:
            logger.warning(f"[Alert/Telegram] HTTP {resp.status_code}: {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"[Alert/Telegram] {e}")
        return False


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(smtp_host: str, smtp_port: int, smtp_user: str,
               smtp_pass: str, to_email: str, subject: str, body: str) -> bool:
    if not all([smtp_host, smtp_user, smtp_pass, to_email]):
        logger.warning("[Alert/Email] Missing SMTP configuration")
        return False
    try:
        msg             = MIMEMultipart("alternative")
        msg["Subject"]  = subject
        msg["From"]     = smtp_user
        msg["To"]       = to_email
        msg.attach(MIMEText(body, "html"))

        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.sendmail(smtp_user, to_email, msg.as_string())
        else:  # 465 SSL
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as s:
                s.login(smtp_user, smtp_pass)
                s.sendmail(smtp_user, to_email, msg.as_string())
        logger.info(f"[Alert/Email] Sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"[Alert/Email] {e}")
        return False


# ── WhatsApp (Twilio) ─────────────────────────────────────────────────────────

def send_whatsapp(to_number: str, message: str) -> bool:
    """
    Send WhatsApp message via Twilio API.
    to_number should be in format: +1234567890 (will be prefixed with whatsapp:)
    """
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_ = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    if not sid or not token:
        logger.warning("[Alert/WhatsApp] Missing Twilio credentials")
        return False

    to_wa = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number

    try:
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        resp = requests.post(url, data={
            "From": from_,
            "To":   to_wa,
            "Body": message,
        }, auth=(sid, token), timeout=10)
        ok = resp.status_code in (200, 201)
        if not ok:
            logger.warning(f"[Alert/WhatsApp] HTTP {resp.status_code}: {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"[Alert/WhatsApp] {e}")
        return False


# ── Main dispatcher ───────────────────────────────────────────────────────────

def fire_alert(alert_config: dict, asset_label: str, asset_icon: str,
               score: float, signal: str, article_title: str) -> bool:
    direction = alert_config.get("direction", "bullish")
    threshold = alert_config.get("threshold", 0.6)
    channel   = alert_config.get("channel", "telegram")
    dest      = alert_config.get("destination", "")

    msgs = build_alert_message(
        asset_label, asset_icon, direction, score,
        signal, article_title, threshold,
    )

    if channel == "telegram":
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        return send_telegram(bot_token, dest, msgs["telegram"])

    elif channel == "email":
        return send_email(
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_pass=os.getenv("SMTP_PASS", ""),
            to_email=dest,
            subject=msgs["subject"],
            body=msgs["email"],
        )

    elif channel == "whatsapp":
        return send_whatsapp(to_number=dest, message=msgs["whatsapp"])

    logger.warning(f"[Alert] Unknown channel: {channel}")
    return False
