import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Handles sending notifications (Email) for critical system events.
    Includes rate limiting to prevent spam.
    """

    _last_sent = {} # Key: (device_id, metric, severity), Value: datetime
    RATE_LIMIT_MINUTES = 15
    
    @classmethod
    def send_alert(cls, device, metric, value, message, severity="CRITICAL"):
        """
        Sends an email notification for CRITICAL/WARNING alerts.
        Uses mock email output and rate limiting to prevent spam.
        """
        severity = (severity or "CRITICAL").upper()
        if severity not in ("CRITICAL", "WARNING"):
            return

        device_id = getattr(device, 'device_id', None)
        if device_id is None:
            device_id = getattr(device, 'id', None)
        device_name = getattr(device, 'device_name', None) or 'Unknown Device'
        device_ip = getattr(device, 'device_ip', None) or getattr(device, 'ip_address', None) or 'Unknown IP'

        rate_key = (device_id, metric, severity)
        if cls._is_rate_limited(rate_key):
            logger.debug("[NotificationService] Alert suppressed for %s (rate limit)", device_ip)
            return

        subject = f"[{severity}] Device {device_name} ({device_ip}) Alert"

        body = f"""
        {severity} ALERT DETECTED
        
        Device: {device_name}
        IP Address: {device_ip}
        Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
        
        Issue: {metric.upper()}
        Value: {value}
        
        Message:
        {message}
        
        --
        Tactical NMS
        """

        if cls._send_email(subject, body):
            cls._last_sent[rate_key] = datetime.utcnow()
            logger.info("[NotificationService] Sent %s alert for %s", severity.lower(), device_ip)
        else:
            logger.warning("[NotificationService] Failed to send alert for %s", device_ip)

    @classmethod
    def send_critical_alert(cls, device, metric, value, message):
        cls.send_alert(device, metric, value, message, severity="CRITICAL")

    @classmethod
    def send_warning_alert(cls, device, metric, value, message):
        cls.send_alert(device, metric, value, message, severity="WARNING")

    @classmethod
    def _get_effective_smtp_config(cls) -> dict:
        """Return SMTP config: DB-backed first, os.environ fallback."""
        try:
            from services.settings_service import get_smtp_config
            config = get_smtp_config()
            if config and config.get('smtp_server'):
                return config
        except Exception:
            pass
        # Env fallback
        return {
            'smtp_server': os.environ.get('SMTP_SERVER', '').strip(),
            'smtp_port': int(os.environ.get('SMTP_PORT', 587)),
            'smtp_user': os.environ.get('SMTP_USER', '').strip(),
            'smtp_password': os.environ.get('SMTP_PASSWORD', '').strip(),
            'smtp_from': os.environ.get('SMTP_FROM', '').strip(),
            'smtp_recipients': os.environ.get('SMTP_RECIPIENTS', '').strip(),
            'smtp_use_tls': os.environ.get('SMTP_USE_TLS', 'true').lower() != 'false',
        }

    @classmethod
    def send_via_channel(cls, channel, device, message: str, severity: str) -> bool:
        """Deliver an alert via a specific AlertChannel row.

        Returns True if delivered, False on failure or unsupported channel type.
        Rate limiting is handled by the caller (alert_routing_service) at the
        device+metric+severity level — not per channel.
        """
        channel_type = (getattr(channel, 'channel_type', '') or '').lower()

        if channel_type == 'email':
            config = getattr(channel, 'config_json', None) or {}
            recipients = config.get('recipients', [])
            if isinstance(recipients, str):
                recipients = [r.strip() for r in recipients.split(',') if r.strip()]
            if not recipients:
                logger.warning('[routing] Email channel "%s" has no recipients configured',
                               getattr(channel, 'name', '?'))
                return False

            device_name = getattr(device, 'device_name', None) or 'Unknown Device'
            device_ip = (getattr(device, 'device_ip', None)
                         or getattr(device, 'ip_address', None)
                         or 'Unknown IP')
            subject = f"[{severity}] Alert: {device_name} ({device_ip})"
            return cls._send_email(subject, message, recipients_override=recipients)

        if channel_type in ('slack', 'teams'):
            logger.info('[routing] %s channel not yet wired — skipping delivery', channel_type)
            return False

        logger.warning('[routing] Unknown channel type "%s" — skipping', channel_type)
        return False

    @classmethod
    def _is_rate_limited(cls, key):
        """Returns True if we sent an email for this key recently."""
        last_time = cls._last_sent.get(key)
        if not last_time:
            return False
            
        if datetime.utcnow() - last_time < timedelta(minutes=cls.RATE_LIMIT_MINUTES):
            return True
            
        return False

    @classmethod
    def _send_email(cls, subject, body, recipients_override=None):
        """Send plain-text email via smtplib. Returns True on success, False on failure.

        `recipients_override` (list[str]) bypasses the global SMTP_RECIPIENTS config,
        used by `send_via_channel()` to deliver to channel-specific address lists.
        """
        cfg = cls._get_effective_smtp_config()
        smtp_server = cfg.get('smtp_server', '')
        if not smtp_server:
            logger.warning('[email] SMTP_SERVER not configured — skipping email send')
            logger.debug('[email] MOCK subject: %s', subject)
            return True

        smtp_port = int(cfg.get('smtp_port', 587))
        smtp_user = cfg.get('smtp_user', '')
        smtp_password = cfg.get('smtp_password', '')
        smtp_from = cfg.get('smtp_from', '') or smtp_user

        if recipients_override:
            recipients = list(recipients_override)
        else:
            recipients_raw = cfg.get('smtp_recipients', '')
            if isinstance(recipients_raw, list):
                recipients = recipients_raw
            else:
                recipients = [r.strip() for r in (recipients_raw or '').split(',') if r.strip()]

        use_tls = cfg.get('smtp_use_tls', True)
        if isinstance(use_tls, str):
            use_tls = use_tls.lower() != 'false'

        if not recipients:
            logger.warning('[email] No recipients configured — skipping email send')
            return True

        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_from
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
            return True
        except Exception as exc:
            logger.error('[email] Failed to send "%s": %s', subject, exc)
            return False
