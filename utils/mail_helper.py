import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = "smtp-relay.brevo.com"
SMTP_PORT = 587
EMAIL_ADDRESS = "a72eba001@smtp-brevo.com" 
# EMAIL_ADDRESS = "abhi23kk@gmail.com"   # Your Brevo SMTP login
EMAIL_PASSWORD = "xsmtpsib-2de4c562cea98a080a239302fedc0c7a91918b80411477fe5904dcb683bcc816-0p7E8kYDAtKDZeX1"  # Your full SMTP key

def send_email(to, subject, body):
    print(f"\n📧 Attempting to send email to: {to}")
    print(f"   Subject: {subject}")
    try:
        msg = MIMEMultipart()
        msg["From"] = "abhi23kk@gmail.com"
        print(f"   From: {msg['From']}")
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        print("   Connecting to SMTP server...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        print("   Logging in...")
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        print("   Sending message...")
        server.send_message(msg)
        server.quit()
        print("✅ Email sent successfully via Brevo")
        return True
    except Exception as e:
        print(f"❌ SMTP error: {e}")
        return False

def send_otp_email(to, otp):
    subject = "Your OTP Code"
    body = f"""
    <div style="font-family: Arial; padding: 20px;">
        <h2>Your OTP Code</h2>
        <h1 style="color: #4df;">{otp}</h1>
        <p>This code expires in 5 minutes.</p>
    </div>
    """
    return send_email(to, subject, body)

def send_reset_email(to, token):
    reset_link = f"http://localhost:5000/reset-password.html?token={token}"
    subject = "Password Reset Request"
    body = f"""
    <div style="font-family: Arial; padding: 20px;">
        <h2>Reset Your Password</h2>
        <p>Click the link below:</p>
        <a href="{reset_link}" style="background: #4df; color: white; padding: 10px 20px; text-decoration: none;">Reset Password</a>
        <p>If you didn't request this, ignore this email.</p>
    </div>
    """
    return send_email(to, subject, body)