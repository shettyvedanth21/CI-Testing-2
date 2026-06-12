from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("auth-service.mailer")


class MailerService:
    def _platform_name(self) -> str:
        return (settings.PLATFORM_NAME or "Shivex").strip() or "Shivex"

    def _assert_configured(self) -> None:
        required = {
            "EMAIL_SMTP_HOST": settings.EMAIL_SMTP_HOST,
            "EMAIL_FROM_ADDRESS": settings.EMAIL_FROM_ADDRESS,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Invite email configuration missing: {missing}")

    def _send(self, recipient: str, subject: str, html: str, text: str) -> None:
        self._assert_configured()
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = settings.EMAIL_FROM_ADDRESS
        message["To"] = recipient
        message.attach(MIMEText(text, "plain"))
        message.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=context)
                server.ehlo()
            if settings.EMAIL_SMTP_USERNAME and settings.EMAIL_SMTP_PASSWORD:
                server.login(settings.EMAIL_SMTP_USERNAME, settings.EMAIL_SMTP_PASSWORD)
            server.sendmail(settings.EMAIL_FROM_ADDRESS, [recipient], message.as_string())

    async def send_invite_email(self, *, recipient: str, full_name: str | None, invite_link: str) -> None:
        greeting = full_name or recipient
        platform_name = self._platform_name()
        subject = f"Your {platform_name} invitation"
        text = (
            f"Hello {greeting},\n\n"
            f"You have been invited to {platform_name}. Use the link below to set your password. "
            f"This link expires in {settings.INVITE_TOKEN_EXPIRE_MINUTES} minutes.\n\n"
            f"{invite_link}\n"
        )
        html = (
            "<html><body>"
            f"<p>Hello {greeting},</p>"
            f"<p>You have been invited to {platform_name}. Use the link below to set your password.</p>"
            f"<p><a href=\"{invite_link}\">Set your password</a></p>"
            f"<p>This link expires in {settings.INVITE_TOKEN_EXPIRE_MINUTES} minutes.</p>"
            "</body></html>"
        )
        await asyncio.to_thread(self._send, recipient, subject, html, text)
        logger.info("Sent invitation email", extra={"recipient": recipient})

    async def send_password_reset_email(self, *, recipient: str, full_name: str | None, reset_link: str) -> None:
        greeting = full_name or recipient
        platform_name = self._platform_name()
        subject = f"Reset your {platform_name} password"
        text = (
            f"Hello {greeting},\n\n"
            f"We received a request to reset your {platform_name} password. "
            f"This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes.\n\n"
            f"{reset_link}\n"
        )
        html = (
            "<html><body>"
            f"<p>Hello {greeting},</p>"
            f"<p>We received a request to reset your {platform_name} password.</p>"
            f"<p><a href=\"{reset_link}\">Reset password</a></p>"
            f"<p>This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes.</p>"
            "</body></html>"
        )
        await asyncio.to_thread(self._send, recipient, subject, html, text)
        logger.info("Sent password reset email", extra={"recipient": recipient})

    async def send_platform_maintenance_email(
        self,
        *,
        recipient: str,
        full_name: str | None,
        title: str,
        severity: str,
        message: str,
        starts_at: datetime,
        estimated_duration_minutes: int,
        status: str,
    ) -> None:
        greeting = full_name or recipient
        platform_name = self._platform_name()
        severity_label = {
            "info": "Heads-up",
            "warning": "Important",
            "critical": "Critical",
        }.get(severity, "Maintenance")
        subject = (
            f"{platform_name} maintenance in progress: {title}"
            if status == "active"
            else f"{severity_label} {platform_name} maintenance: {title}"
        )
        start_label = starts_at.astimezone().strftime("%d %b %Y, %I:%M %p %Z") if starts_at.tzinfo else starts_at.isoformat()
        duration_label = (
            f"{estimated_duration_minutes} minutes"
            if estimated_duration_minutes < 60
            else (
                f"{estimated_duration_minutes // 60} hour{'s' if estimated_duration_minutes // 60 != 1 else ''}"
                if estimated_duration_minutes % 60 == 0
                else f"{estimated_duration_minutes // 60}h {estimated_duration_minutes % 60}m"
            )
        )
        lead = (
            "Platform maintenance is now in progress."
            if status == "active"
            else "We’re sharing an upcoming maintenance notice for your organisation."
        )
        text = (
            f"Hello {greeting},\n\n"
            f"{lead}\n\n"
            f"Title: {title}\n"
            f"Severity: {severity_label}\n"
            f"Starts: {start_label}\n"
            f"Expected duration: {duration_label}\n\n"
            f"{message}\n"
        )
        html = (
            "<html><body style=\"font-family:Arial,sans-serif;color:#0f172a;line-height:1.6;\">"
            f"<p>Hello {greeting},</p>"
            f"<p>{lead}</p>"
            "<div style=\"border:1px solid #e2e8f0;border-radius:16px;padding:16px;background:#f8fafc;\">"
            f"<p style=\"margin:0 0 8px 0;font-size:12px;font-weight:700;text-transform:uppercase;color:#475569;\">{severity_label}</p>"
            f"<p style=\"margin:0 0 8px 0;font-size:18px;font-weight:700;color:#0f172a;\">{title}</p>"
            f"<p style=\"margin:0 0 12px 0;color:#334155;white-space:pre-wrap;\">{message}</p>"
            f"<p style=\"margin:0;color:#334155;\"><strong>Starts:</strong> {start_label}<br />"
            f"<strong>Expected duration:</strong> {duration_label}</p>"
            "</div>"
            f"<p style=\"margin-top:16px;color:#475569;\">This notice was sent from {platform_name}.</p>"
            "</body></html>"
        )
        await asyncio.to_thread(self._send, recipient, subject, html, text)
        logger.info("Sent platform maintenance email", extra={"recipient": recipient, "title": title, "status": status})


mailer_svc = MailerService()
