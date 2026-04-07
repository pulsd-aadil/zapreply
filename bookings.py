# ============================================================
# bookings.py - Saves and loads all bookings
# ============================================================

import json
import os
from datetime import datetime

BOOKINGS_FILE = "bookings.json"

def save_booking(name, phone, date, time, party_size):
    """Save a new booking to file"""
    bookings = load_all_bookings()
    booking = {
        "id": len(bookings) + 1,
        "name": name,
        "phone": phone,
        "date": date,
        "time": time,
        "party_size": party_size,
        "status": "Active",
        "created_at": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    bookings.append(booking)
    with open(BOOKINGS_FILE, "w") as f:
        json.dump(bookings, f, indent=2, ensure_ascii=False)
    print(f"Booking saved: {name} - {date} at {time}")
    return booking

def cancel_booking(phone):
    """Cancel most recent active booking for this phone number"""
    bookings = load_all_bookings()
    for booking in reversed(bookings):
        if booking["phone"] == phone and booking["status"] == "Active":
            booking["status"] = "Cancelled"
            break
    with open(BOOKINGS_FILE, "w") as f:
        json.dump(bookings, f, indent=2, ensure_ascii=False)
    print(f"Booking cancelled for {phone}")

def load_all_bookings():
    """Load all bookings from file"""
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r") as f:
            return json.load(f)
    return []

def get_todays_bookings():
    """Get only today's bookings"""
    today = datetime.now().strftime("%d/%m/%Y")
    all_bookings = load_all_bookings()
    return [b for b in all_bookings if today in str(b.get("date", ""))]