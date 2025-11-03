import smtplib
from email.mime.text import MIMEText
from config import EMAIL

def send_email_alert(subject: str, body: str):
    # Skip if no email is set
    if not EMAIL.strip():
        return

    sender = "flight.tracker.alerts2025@gmail.com"
    password = "wlst ujvs bcvu uhdr"
    receiver = EMAIL

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"⚠️ Failed to send email: {e}")
