import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

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

        rate_key = (device.device_id, metric, severity)
        if cls._is_rate_limited(rate_key):
            print(f"[NOTE] Alert email suppressed for {device.device_ip} (Rate Limit)")
            return

        subject = f"[{severity}] Device {device.device_name} ({device.device_ip}) Alert"

        body = f"""
        {severity} ALERT DETECTED
        
        Device: {device.device_name}
        IP Address: {device.device_ip}
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
            print(f"[EMAIL] Sent {severity.lower()} alert for {device.device_ip}")
        else:
            print(f"[EMAIL] Failed to send alert for {device.device_ip}")

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
        """Internal method to send plain text email."""
        # For this tactical deployment, we might not have a real SMTP server.
        # We will simulate success and print to console to prove logic flows.
        
        # Real implementation would be:
        # try:
        #     msg = MIMEMultipart()
        #     msg['From'] = cls.SMTP_USER
        #     msg['To'] = ", ".join(cls.RECIPIENTS)
        #     msg['Subject'] = subject
        #     msg.attach(MIMEText(body, 'plain'))
        #     
        #     server = smtplib.SMTP(cls.SMTP_SERVER, cls.SMTP_PORT)
        #     server.starttls()
        #     server.login(cls.SMTP_USER, cls.SMTP_PASS)
        #     server.send_message(msg)
        #     server.quit()
        #     return True
        # except Exception as e:
        #     print(f"SMTP Error: {e}")
        #     return False
        
        # Simulation
        print(f"--- [MOCK EMAIL] ---\nTo: {cls.RECIPIENTS}\nSubject: {subject}\n{body}\n--------------------")
        return True
