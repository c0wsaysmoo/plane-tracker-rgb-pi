import smtplib
import socket
from email.mime.text import MIMEText
from datetime import datetime
from config import EMAIL, DISTANCE_UNITS, CLOCK_FORMAT
from typing import Optional

def get_timestamp():
    if CLOCK_FORMAT == "24hr":
        return datetime.now().strftime("%b %d %Y, %H:%M:%S")
    return datetime.now().strftime("%b %d %Y, %I:%M:%S %p")


def format_dist(v):
    if DISTANCE_UNITS.lower() == "metric":
        return f"{v:.5f} km"
    return f"{v:.5f} miles"


def _send(subject: str, body: str):
    """Low-level email sender (internal)."""
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


def send_flight_summary(subject: str, entry: dict, reason: Optional[str] = None):
    """Formats + sends a flight summary email."""
    hostname = socket.gethostname()

    body = (
        f"Timestamp: {entry.get('timestamp')}\n"
        f"Hostname: {hostname}\n"
        f"Airline: {entry.get('airline','N/A')}\n"
        f"Flight: {entry.get('callsign','N/A')}\n"
        f"From: {entry.get('origin','?')}\n"
        f"To: {entry.get('destination','?')}\n"
        f"Plane: {entry.get('plane','N/A')}\n"
    )

    if reason:
        body += f"Reason: {reason}\n"

    # Add distances if supplied
    if "distance_origin" in entry:
        body += f"Distance_origin: {format_dist(entry['distance_origin'])}\n"
    if "distance_destination" in entry:
        body += f"Distance_destination: {format_dist(entry['distance_destination'])}\n"
    if "distance" in entry:
        body += f"Distance: {format_dist(entry['distance'])}\n"

    body += f"Direction: {entry.get('direction','N/A')}\n"

    _send(subject, body)

