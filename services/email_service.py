import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
from config import Config

def send_otp_email(email, otp):
    """Send OTP email for password reset"""
    sender_email = Config.SMTP_USERNAME
    receiver_email = email
    password = Config.SMTP_PASSWORD
    
    subject = "APL Techno: OTP for Password Reset Request"
    body = f"""
Hello,

We received a request to reset the password for your APL Techno account.

Your One-Time Password (OTP) for password reset is: {otp}.

Please use this OTP within the next 10 minutes to complete the password reset process.

Thank you for using APL Techno!

Best regards,
The APL Techno Team
"""

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    # MOCK EMAIL LOGGING (For development/testing)
    print(f"\n--- [MOCK EMAIL] OTP for {email}: {otp} ---\n")
    
    try:
        if not sender_email or not password:
            print("DEBUG: SMTP credentials not set, skipping real email send.")
            return

        server = smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        print(f"OTP email sent to {email}")
    except Exception as e:
        print(f"Error sending OTP email: {e}")

def send_alert_email(email, subject, message):
    """Send alert email for device monitoring"""
    sender_email = Config.SMTP_USERNAME
    receiver_email = email
    password = Config.SMTP_PASSWORD
    
    body = f"""
Hello,

{message}

Thank you for using APL Techno!

Best regards,
The APL Techno Team
"""

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"APL Techno Alert: {subject}"
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        print(f"Alert email sent to {email}")
    except Exception as e:
        print(f"Error sending alert email: {e}")

def send_otp_email_async(email, otp):
    """Send OTP email asynchronously"""
    thread = threading.Thread(target=send_otp_email, args=(email, otp))
    thread.start()

def send_alert_email_async(email, subject, message):
    """Send alert email asynchronously"""
    thread = threading.Thread(target=send_alert_email, args=(email, subject, message))
    thread.start()