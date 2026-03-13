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
    
    # SMTP Configuration (In production, load from DB/Env)
    SMTP_SERVER = "smtp.example.com"
    SMTP_PORT = 587
    SMTP_USER = "alerts@example.com"
    SMTP_PASS = "password"
    RECIPIENTS = ["admin@example.com"]
    
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
            print(f"[NOTE] Alert email suppressed for {device_ip} (Rate Limit)")
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
            print(f"[EMAIL] Sent {severity.lower()} alert for {device_ip}")
        else:
            print(f"[EMAIL] Failed to send alert for {device_ip}")

    @classmethod
    def send_critical_alert(cls, device, metric, value, message):
        cls.send_alert(device, metric, value, message, severity="CRITICAL")

    @classmethod
    def send_warning_alert(cls, device, metric, value, message):
        cls.send_alert(device, metric, value, message, severity="WARNING")

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
    def _send_email(cls, subject, body):
        """Send plain-text email via smtplib. Returns True on success, False on failure."""
        smtp_server = os.environ.get('SMTP_SERVER', '').strip()
        if not smtp_server:
            logger.warning('[email] SMTP_SERVER not set — skipping email send')
            logger.debug('[email] MOCK: %s', subject)
            return True

        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_user = os.environ.get('SMTP_USER', '').strip()
        smtp_password = os.environ.get('SMTP_PASSWORD', '').strip()
        smtp_from = os.environ.get('SMTP_FROM', '').strip() or smtp_user
        recipients_raw = os.environ.get('SMTP_RECIPIENTS', '').strip()
        recipients = [r.strip() for r in recipients_raw.split(',') if r.strip()]
        use_tls = os.environ.get('SMTP_USE_TLS', 'true').lower() != 'false'

        if not recipients:
            logger.warning('[email] SMTP_RECIPIENTS not set — skipping email send')
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
