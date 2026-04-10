# ============================================================
# ZapReply - AI WhatsApp Assistant
# Powered by Groq AI (Free + Fast)
# ============================================================

from flask import Flask, request, render_template_string
from groq import Groq
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
import os
import json
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from bookings import save_booking, load_all_bookings, get_todays_bookings, cancel_booking, update_booking, count_todays_bookings

load_dotenv()
app = Flask(__name__)
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

BUSINESS_PROMPT = """
You are the customer service assistant for Tasty Bites restaurant
located in Khalidiyah, Abu Dhabi, UAE.

CRITICAL RULES:
- If customer writes in Arabic reply ONLY in Arabic
- If customer writes in English reply ONLY in English
- Keep replies SHORT - this is WhatsApp not email
- Be warm friendly and professional
- Maximum 2 emojis per message
- Never make up information

BUSINESS INFO:
- Name: Tasty Bites Restaurant
- Location: Khalidiyah Mall area, Abu Dhabi
- Hours: 12pm to 11pm daily
- Food: Lebanese and International cuisine
- Delivery: Talabat and Careem
- Reservations: Yes we accept bookings

BOOKING FLOW - follow this exact order:
Step 1 - Ask for customer name
Step 2 - Ask for date
Step 3 - Ask for time
Step 4 - Ask for number of people
Step 5 - Confirm everything back with a summary
Step 6 - End with: Your booking is confirmed! See you then!

CANCELLATION FLOW - when customer wants to cancel:
Step 1 - Ask for their name
Step 2 - Ask for the date and time of their booking
Step 3 - Confirm with: Your booking has been cancelled. We hope to see you another time!

IF YOU CANNOT HELP:
- Say: I will pass this to our manager who will reply shortly
- Never invent information you are not sure about

AFTER HOURS BEHAVIOUR:
- If it is currently after hours (before 12pm or after 11pm), start your reply with:
  "We are currently closed but I can still help you! 🌙"
- Then continue to help them normally — answer questions and take bookings
- For bookings after hours, make sure the booking is for when we next open (12pm or later)
- Never refuse to help just because it is after hours
"""

conversation_history = {}

STATS_FILE = "stats.json"

def load_stats():
    """Load stats from file so they persist after restart"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            saved = json.load(f)
            return {
                "messages": saved.get("messages", 0),
                "bookings": saved.get("bookings", 0),
                "escalations": saved.get("escalations", 0),
                "conversations": {}
            }
    return {
        "messages": 0,
        "bookings": 0,
        "escalations": 0,
        "conversations": {}
    }

def save_stats():
    """Save stats to file"""
    with open(STATS_FILE, "w") as f:
        json.dump({
            "messages": stats["messages"],
            "bookings": stats["bookings"],
            "escalations": stats["escalations"]
        }, f)

stats = load_stats()

# Start background scheduler for reminders and daily report
scheduler = BackgroundScheduler()
scheduler.start()

def send_whatsapp(to_number, message):
    """Send a WhatsApp message to any number"""
    try:
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        client.messages.create(
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{to_number}",
            body=message
        )
        print(f"Message sent to {to_number}")
    except Exception as e:
        print(f"Send failed: {e}")

def send_appointment_reminders():
    """Check bookings and send reminders 1hr before"""
    try:
        now = datetime.now()
        current_time = now.strftime("%I:%M %p").lstrip("0")
        all_bookings = load_all_bookings()
        for booking in all_bookings:
            if booking["status"] != "Active":
                continue
            # Check if booking is in ~1 hour
            booking_time = booking.get("time", "")
            booking_date = booking.get("date", "")
            today_str = now.strftime("%d %b %y")
            if today_str.lower() in booking_date.lower():
                try:
                    from datetime import timedelta
                    booking_dt = datetime.strptime(
                        f"{now.strftime('%Y-%m-%d')} {booking_time}",
                        "%Y-%m-%d %I:%M %p"
                    )
                    diff = (booking_dt - now).total_seconds() / 60
                    if 55 <= diff <= 65:  # Within 1 hour window
                        reminder = (
                            f"Hi {booking['name']}! 🔔 Reminder: "
                            f"Your table at Tasty Bites is booked for today at {booking_time}. "
                            f"We look forward to seeing you!"
                        )
                        send_whatsapp(booking["phone"].replace("whatsapp:", ""), reminder)
                        print(f"Reminder sent to {booking['name']}")
                except Exception as e:
                    print(f"Reminder error: {e}")
    except Exception as e:
        print(f"Scheduler error: {e}")

def send_daily_report():
    """Send daily summary to owner at 8pm"""
    try:
        owner = os.getenv("OWNER_WHATSAPP")
        today_bookings = get_todays_bookings()
        total = load_all_bookings()
        report = (
            f"📊 Daily Report — {datetime.now().strftime('%d %b %Y')}\n"
            f"─────────────────────\n"
            f"👥 Customers reached: {stats['messages']}\n"
            f"✅ Today's bookings: {len(today_bookings)}\n"
            f"📅 Total bookings: {stats['bookings']}\n"
            f"⚠️  Escalations: {stats['escalations']}\n"
            f"\nYour AI worked all day so you didn't have to! 🤖"
        )
        send_whatsapp(owner, report)
        print("Daily report sent to owner!")
    except Exception as e:
        print(f"Daily report error: {e}")

# Schedule reminder check every minute
scheduler.add_job(send_appointment_reminders, 'interval', minutes=1)

# Schedule daily report at 8pm every day
scheduler.add_job(send_daily_report, 'cron', hour=20, minute=0)

def ordinal(n):
    """Convert number to ordinal string: 1 -> 1st, 2 -> 2nd etc"""
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n%10]}"

def format_date_string(date_obj):
    """Format a datetime object to '9th Apr 26 (Thu)'"""
    day = ordinal(date_obj.day)
    return date_obj.strftime(f"{day} %b %y (%a)")

def parse_date_from_text(raw_date):
    """Convert any date input to formatted date string"""
    try:
        from dateutil import parser as dateparser
        from dateutil.relativedelta import relativedelta
        today = datetime.now()
        raw = raw_date.lower().strip()

        # Handle relative day names
        days = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,
                "friday":4,"saturday":5,"sunday":6,
                "mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}

        # "next monday" = Monday after the coming Monday
        if raw.startswith("next "):
            day_name = raw.replace("next ", "").strip()
            if day_name in days:
                target = days[day_name]
                days_ahead = (target - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                coming = today + __import__('datetime').timedelta(days=days_ahead)
                result = coming + __import__('datetime').timedelta(days=7)
                return format_date_string(result)

        # "coming monday" or just "monday" = nearest upcoming day
        for prefix in ["coming ", "this "]:
            if raw.startswith(prefix):
                raw = raw.replace(prefix, "").strip()

        if raw in days:
            target = days[raw]
            days_ahead = (target - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            result = today + __import__('datetime').timedelta(days=days_ahead)
            return format_date_string(result)

        # "tomorrow"
        if raw == "tomorrow":
            return format_date_string(today + __import__('datetime').timedelta(days=1))

        # "today"
        if raw == "today":
            return format_date_string(today)

        # Try parsing any other format like "9th april", "09/04", "09/04/26"
        parsed = dateparser.parse(raw_date, default=today)
        if parsed:
            # If year is in the past, add 1 year
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
            return format_date_string(parsed)

        return raw_date
    except Exception as e:
        print(f"Date parse error: {e}")
        return raw_date

def extract_detail(conversation, detail_type):
    try:
        today = datetime.now()
        today_str = today.strftime("%A %d %B %Y")
        prompts = {
            "name": "From this WhatsApp conversation extract ONLY the customer name. Reply with just the name and nothing else. No extra words.",
            "date": f"Today is {today_str}. From this conversation what date did the customer mention for their booking? Reply with ONLY the raw date they mentioned. Examples: 'monday' or 'next friday' or '9th april' or '09/04'. Nothing else.",
            "time": "From this WhatsApp conversation extract ONLY the booking time. Format it as 7:00 PM. Reply with just the time nothing else.",
            "party": "From this WhatsApp conversation extract ONLY the number of people for the booking. Reply with just the number nothing else."
        }
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompts[detail_type]},
                {"role": "user", "content": conversation}
            ],
            max_tokens=20
        )
        result = response.choices[0].message.content.strip()
        result = result.replace('"', '').replace("'", '').strip()

        # For dates, run through our Python formatter
        if detail_type == "date" and result:
            result = parse_date_from_text(result)

        return result if result else "Unknown"
    except Exception as e:
        print(f"Extract error ({detail_type}): {e}")
        return "Unknown"

def needs_escalation(message):
    triggers = [
        "angry", "terrible", "horrible", "refund", "manager",
        "unacceptable", "worst", "complaint", "disgusting",
        "غاضب", "مدير", "شكوى", "استرداد", "سيء", "فظيع"
    ]
    for trigger in triggers:
        if trigger in message.lower():
            return True
    return False

def alert_owner(customer_number, customer_message):
    try:
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        client.messages.create(
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{os.getenv('OWNER_WHATSAPP')}",
            body=f"ALERT\nCustomer: {customer_number}\nMessage: {customer_message}"
        )
        print("Owner alerted")
    except Exception as e:
        print(f"Alert failed: {e}")

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    customer_message = request.form.get("Body", "").strip()
    customer_number = request.form.get("From", "")
    timestamp = datetime.now().strftime("%H:%M")

    print(f"\n[{timestamp}] FROM: {customer_number}")
    print(f"MESSAGE: {customer_message}")

    if not customer_message:
        customer_message = "Hello"

    # After-hours check - add note to prompt but still serve customer
    current_hour = datetime.now().hour
    is_after_hours = current_hour < 12 or current_hour >= 23

    # Count unique customers only
    if customer_number not in stats["conversations"] or len(stats["conversations"].get(customer_number, [])) == 0:
        stats["messages"] += 1
        save_stats()

    # Store for dashboard
    if customer_number not in stats["conversations"]:
        stats["conversations"][customer_number] = []
    stats["conversations"][customer_number].append({
        "role": "customer", "text": customer_message, "time": timestamp
    })

    # Memory
    if customer_number not in conversation_history:
        conversation_history[customer_number] = []

    conversation_history[customer_number].append({
        "role": "user", "content": customer_message
    })

    if len(conversation_history[customer_number]) > 10:
        conversation_history[customer_number] = \
            conversation_history[customer_number][-10:]

    # Escalation
    if needs_escalation(customer_message):
        stats["escalations"] += 1
        alert_owner(customer_number, customer_message)

    # Call Groq AI
    try:
        system_prompt = BUSINESS_PROMPT
        if is_after_hours:
            system_prompt += f"\n\nIMPORTANT: It is currently after hours ({datetime.now().strftime('%I:%M %p')}). We are closed right now. Remind the customer we are closed but still help them and take bookings for when we next open."

        messages = [{"role": "system", "content": system_prompt}]
        messages += conversation_history[customer_number] 

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=500
        )
        ai_reply = response.choices[0].message.content

        conversation_history[customer_number].append({
            "role": "assistant", "content": ai_reply
        })

        stats["conversations"][customer_number].append({
            "role": "ai", "text": ai_reply, "time": timestamp
        })

        # Booking confirmed — save with real details
        confirmation_keywords = [
            "your booking is confirmed",
            "booking is confirmed",
            "see you then",
            "see you soon",
            "تم تأكيد حجزك",
            "حجزك مؤكد"
        ]
        if any(kw.lower() in ai_reply.lower() for kw in confirmation_keywords):
            full_convo = "\n".join([
                f"{'Customer' if m['role'] == 'user' else 'AI'}: {m['content']}"
                for m in conversation_history[customer_number]
            ])
            name  = extract_detail(full_convo, "name")
            date  = extract_detail(full_convo, "date")
            time  = extract_detail(full_convo, "time")
            party = extract_detail(full_convo, "party")

            print(f"Extracted — Name: {name} | Date: {date} | Time: {time} | Party: {party}")

            save_booking(
                name=name,
                phone=customer_number,
                date=date,
                time=time,
                party_size=party
            )
            stats["bookings"] += 1
            save_stats()
            print("New booking created!")

            # Clear conversation history after booking
            # So next booking starts fresh
            conversation_history[customer_number] = []
            print("Conversation cleared for fresh start!")

        # Cancellation confirmed
        cancellation_keywords = [
            "booking has been cancelled",
            "reservation has been cancelled",
            "successfully cancelled",
            "تم إلغاء الحجز"
        ]
        if any(kw.lower() in ai_reply.lower() for kw in cancellation_keywords):
            if stats["bookings"] > 0:
                stats["bookings"] -= 1
            save_stats()
            cancel_booking(customer_number)
            print("Booking cancelled!")

    except Exception as e:
        print(f"Groq error: {e}")
        ai_reply = "Thank you for your message! We will reply shortly."

    print(f"AI REPLY: {ai_reply[:80]}...")

    twilio_response = MessagingResponse()
    twilio_response.message(ai_reply)
    return str(twilio_response)

@app.route("/stats")
def get_stats():
    from flask import jsonify
    return jsonify({
        "messages": stats["messages"],
        "bookings": stats["bookings"],
        "escalations": stats["escalations"],
        "todays_bookings": count_todays_bookings()
    })

@app.route("/")
def dashboard():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>ZapReply</title>
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { font-family:Arial,sans-serif; background:#f0f0f0; padding:16px; }
            .header { background:#25D366; color:white; padding:20px;
                      border-radius:12px; margin-bottom:16px; }
            .header h1 { font-size:22px; }
            .header p { font-size:13px; opacity:0.9; margin-top:4px; }
            .stats { display:grid; grid-template-columns:repeat(4,1fr);
                     gap:10px; margin-bottom:16px; }
            .stat-card { background:white; padding:16px; border-radius:10px;
                         text-align:center; box-shadow:0 2px 4px rgba(0,0,0,0.1); }
            .stat-number { font-size:32px; font-weight:bold; color:#25D366; }
            .stat-label { font-size:11px; color:#666; margin-top:4px; }
            .card { background:white; padding:16px; border-radius:10px;
                    margin-bottom:12px; box-shadow:0 2px 4px rgba(0,0,0,0.1); }
            .card h2 { font-size:15px; color:#333; margin-bottom:12px;
                       border-bottom:2px solid #25D366; padding-bottom:8px; }
            .message { padding:8px 12px; border-radius:8px; margin:6px 0;
                       font-size:13px; }
            .customer-msg { background:#f0f0f0; border-left:3px solid #666; }
            .ai-msg { background:#e8f8f0; border-left:3px solid #25D366; }
            .convo-header { font-size:11px; color:#999; margin-top:10px;
                            margin-bottom:4px; font-weight:bold; }
            .status { background:#e8f8f0; color:#25D366; padding:8px 12px;
                      border-radius:8px; font-size:13px; font-weight:bold; }
            table { width:100%; border-collapse:collapse; font-size:13px; }
            td { padding:8px; border-bottom:1px solid #eee; }
            th { padding:8px; background:#f4f4f4; font-weight:bold; text-align:left; }
        </style>
        
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
        <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <div class="header">
            <h1>ZapReply Dashboard</h1>
            <p>Tasty Bites — Powered by Groq AI</p>
        </div>

        <!-- TAB NAVIGATION -->
        <div style="display:flex;gap:8px;margin-bottom:16px;overflow-x:auto;">
            <button onclick="showTab('overview')" id="tab-overview"
                style="padding:10px 20px;border:none;border-radius:8px;
                       background:#25D366;color:white;font-weight:bold;
                       font-size:13px;cursor:pointer;white-space:nowrap;">
                Overview
            </button>
            <button onclick="showTab('bookings')" id="tab-bookings"
                style="padding:10px 20px;border:none;border-radius:8px;
                       background:white;color:#333;font-weight:bold;
                       font-size:13px;cursor:pointer;white-space:nowrap;">
                All Bookings
            </button>
            <button onclick="showTab('inbox')" id="tab-inbox"
                style="padding:10px 20px;border:none;border-radius:8px;
                       background:white;color:#333;font-weight:bold;
                       font-size:13px;cursor:pointer;white-space:nowrap;">
                Inbox
            </button>
            <button onclick="showTab('analytics')" id="tab-analytics"
                style="padding:10px 20px;border:none;border-radius:8px;
                       background:white;color:#333;font-weight:bold;
                       font-size:13px;cursor:pointer;white-space:nowrap;">
                Analytics
            </button>
            <button onclick="showTab('settings')" id="tab-settings"
                style="padding:10px 20px;border:none;border-radius:8px;
                       background:white;color:#333;font-weight:bold;
                       font-size:13px;cursor:pointer;white-space:nowrap;">
                Settings
            </button>
        </div>

        <!-- OVERVIEW TAB -->
        <div id="section-overview">
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{{ messages }}</div>
                <div class="stat-label">Customers Reached</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ bookings }}</div>
                <div class="stat-label">Total Bookings</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ todays_bookings }}</div>
                <div class="stat-label">Today's Bookings</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ escalations }}</div>
                <div class="stat-label">Alerts</div>
            </div>
        </div>
        <div class="card">
            <h2>System Status</h2>
            <div class="status">AI Online — Groq Llama 3.3</div>
        </div>
        </div>
        <!-- END OVERVIEW TAB -->

        <!-- BOOKINGS TAB -->
        <div id="section-bookings" style="display:none;">
        <div class="card">
            <h2>All Bookings</h2>

            <!-- SEARCH AND FILTER BAR -->
            <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
                <input
                    type="text"
                    id="searchName"
                    placeholder="Search by name..."
                    onkeyup="filterBookings()"
                    style="flex:1;padding:8px 12px;border:1px solid #ddd;
                           border-radius:8px;font-size:13px;min-width:120px;">

                <input
                    type="date"
                    id="filterDate"
                    onchange="filterBookings()"
                    style="padding:8px 12px;border:1px solid #ddd;
                           border-radius:8px;font-size:13px;background:white;">

                <div style="display:flex;align-items:center;gap:4px;">
                <select
                    id="filterTime"
                    onchange="filterBookings()"
                    style="padding:8px 12px;border:1px solid #ddd;
                           border-radius:8px;font-size:13px;background:white;">
                    <option value="">Time</option>
                    <optgroup label="AM">
                    <option value="12:00 AM">12:00 AM</option>
                    <option value="12:30 AM">12:30 AM</option>
                    <option value="1:00 AM">1:00 AM</option>
                    <option value="1:30 AM">1:30 AM</option>
                    <option value="2:00 AM">2:00 AM</option>
                    <option value="2:30 AM">2:30 AM</option>
                    <option value="3:00 AM">3:00 AM</option>
                    <option value="3:30 AM">3:30 AM</option>
                    <option value="4:00 AM">4:00 AM</option>
                    <option value="4:30 AM">4:30 AM</option>
                    <option value="5:00 AM">5:00 AM</option>
                    <option value="5:30 AM">5:30 AM</option>
                    <option value="6:00 AM">6:00 AM</option>
                    <option value="6:30 AM">6:30 AM</option>
                    <option value="7:00 AM">7:00 AM</option>
                    <option value="7:30 AM">7:30 AM</option>
                    <option value="8:00 AM">8:00 AM</option>
                    <option value="8:30 AM">8:30 AM</option>
                    <option value="9:00 AM">9:00 AM</option>
                    <option value="9:30 AM">9:30 AM</option>
                    <option value="10:00 AM">10:00 AM</option>
                    <option value="10:30 AM">10:30 AM</option>
                    <option value="11:00 AM">11:00 AM</option>
                    <option value="11:30 AM">11:30 AM</option>
                    </optgroup>
                    <optgroup label="PM">
                    <option value="12:00 PM">12:00 PM</option>
                    <option value="12:30 PM">12:30 PM</option>
                    <option value="1:00 PM">1:00 PM</option>
                    <option value="1:30 PM">1:30 PM</option>
                    <option value="2:00 PM">2:00 PM</option>
                    <option value="2:30 PM">2:30 PM</option>
                    <option value="3:00 PM">3:00 PM</option>
                    <option value="3:30 PM">3:30 PM</option>
                    <option value="4:00 PM">4:00 PM</option>
                    <option value="4:30 PM">4:30 PM</option>
                    <option value="5:00 PM">5:00 PM</option>
                    <option value="5:30 PM">5:30 PM</option>
                    <option value="6:00 PM">6:00 PM</option>
                    <option value="6:30 PM">6:30 PM</option>
                    <option value="7:00 PM">7:00 PM</option>
                    <option value="7:30 PM">7:30 PM</option>
                    <option value="8:00 PM">8:00 PM</option>
                    <option value="8:30 PM">8:30 PM</option>
                    <option value="9:00 PM">9:00 PM</option>
                    <option value="9:30 PM">9:30 PM</option>
                    <option value="10:00 PM">10:00 PM</option>
                    <option value="10:30 PM">10:30 PM</option>
                    <option value="11:00 PM">11:00 PM</option>
                    <option value="11:30 PM">11:30 PM</option>
                    </optgroup>
                    
                </select>
                
                
                </div>

                <select
                    id="filterStatus"
                    onchange="filterBookings()"
                    style="padding:8px 12px;border:1px solid #ddd;
                           border-radius:8px;font-size:13px;background:white;">
                    <option value="">All Status</option>
                    <option value="Active">Active</option>
                    <option value="Cancelled">Cancelled</option>
                </select>
            </div>

            {% if all_bookings %}
            <div style="max-height:220px;overflow-y:scroll;border:1px solid #eee;border-radius:8px;">
            <table id="bookingsTable">
                <tr>
                    <th>Name</th>
                    <th>Date</th>
                    <th>Time</th>
                    <th>People</th>
                    <th>Status</th>
                </tr>
                {% for b in all_bookings|reverse %}
                <tr class="booking-row"
                    data-name="{{ b.name|lower }}"
                    data-date="{{ b.date }}"
                    data-time="{{ b.time }}"
                    data-status="{{ b.status }}">
                    <td>{{ b.name }}</td>
                    <td>{{ b.date }}</td>
                    <td>{{ b.time }}</td>
                    <td>{{ b.party_size }}</td>
                    <td>
                        {% if b.status == "Active" %}
                            <span style="background:#e8f8f0;color:#25D366;
                                padding:3px 8px;border-radius:10px;
                                font-weight:bold;font-size:11px;">
                                Active
                            </span>
                        {% else %}
                            <span style="background:#fdecea;color:#c0392b;
                                padding:3px 8px;border-radius:10px;
                                font-weight:bold;font-size:11px;">
                                Cancelled
                            </span>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
            </div>
            <p id="noResults" style="color:#999;font-size:13px;
               display:none;padding:8px;">No bookings match your search.</p>
            {% else %}
                <p style="color:#999;font-size:13px;">No bookings yet.</p>
            {% endif %}
        </div>

       <script>
        // Bookings Chart Data
        var bookingDates = [];
        var bookingCounts = {};

        {% for b in all_bookings %}
        (function() {
            var d = "{{ b.date }}".split(" ").slice(0,3).join(" ");
            if (!bookingCounts[d]) bookingCounts[d] = 0;
            bookingCounts[d]++;
        })();
        {% endfor %}

        var sortedDates = Object.keys(bookingCounts).slice(-7);
        var sortedCounts = sortedDates.map(function(d) { return bookingCounts[d]; });

        var ctx = document.getElementById('bookingsChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: sortedDates.length > 0 ? sortedDates : ['No data yet'],
                    datasets: [{
                        label: 'Bookings',
                        data: sortedCounts.length > 0 ? sortedCounts : [0],
                        backgroundColor: [
                            '#25D366','#0f3460','#e67e22',
                            '#8e44ad','#c0392b','#2980b9','#27ae60'
                        ],
                        borderRadius: 8,
                        borderSkipped: false,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(c) {
                                    return c.parsed.y + ' booking' + (c.parsed.y !== 1 ? 's' : '');
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { stepSize: 1 },
                            grid: { color: '#f0f0f0' }
                        },
                        x: {
                            grid: { display: false }
                        }
                    }
                }
            });
        }
window.addEventListener('load', function() {
            var pieCtx = document.getElementById('statusPieChart');
            if (pieCtx) {
                var activeCount = {{ all_bookings|selectattr("status","eq","Active")|list|length }};
                var cancelledCount = {{ all_bookings|selectattr("status","eq","Cancelled")|list|length }};
                var totalCount = activeCount + cancelledCount;

                var pieChart = new Chart(pieCtx, {
                    type: 'doughnut',
                    data: {
                        labels: ['Active', 'Cancelled'],
                        datasets: [{
                            data: [activeCount, cancelledCount],
                            backgroundColor: ['#25D366', '#c0392b'],
                            borderWidth: 3,
                            borderColor: '#fff',
                            hoverOffset: 8
                        }]
                    },
                    options: {
                        responsive: true,
                        cutout: '65%',
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                callbacks: {
                                    label: function(c) {
                                        var pct = totalCount > 0 ?
                                            Math.round(c.parsed / totalCount * 100) : 0;
                                        return c.label + ': ' + c.parsed + ' (' + pct + '%)';
                                    }
                                }
                            }
                        }
                    },
                    plugins: [{
                        id: 'centerText',
                        afterDraw: function(chart) {
                            var ctx = chart.ctx;
                            var cx = chart.chartArea.left +
                                (chart.chartArea.right - chart.chartArea.left) / 2;
                            var cy = chart.chartArea.top +
                                (chart.chartArea.bottom - chart.chartArea.top) / 2;
                            var activePct = totalCount > 0 ?
                                Math.round(activeCount / totalCount * 100) : 0;
                            ctx.save();
                            ctx.font = 'bold 22px Arial';
                            ctx.fillStyle = '#25D366';
                            ctx.textAlign = 'center';
                            ctx.textBaseline = 'middle';
                            ctx.fillText(activePct + '%', cx, cy - 8);
                            ctx.font = '11px Arial';
                            ctx.fillStyle = '#999';
                            ctx.fillText('Active', cx, cy + 12);
                            ctx.restore();
                        }
                    }]
                });
            }
        });
        function showTab(tab) {
            // Hide all sections
            var sections = ['overview', 'bookings', 'inbox', 'analytics', 'settings'];
            sections.forEach(function(s) {
                var el = document.getElementById('section-' + s);
                if (el) el.style.display = 'none';
                var btn = document.getElementById('tab-' + s);
                if (btn) {
                    btn.style.background = 'white';
                    btn.style.color = '#333';
                }
            });
            // Hide overview convos if not overview
            var overviewConvos = document.getElementById('section-overview-convos');
            if (overviewConvos) {
                overviewConvos.style.display = tab === 'overview' ? 'block' : 'none';
            }
            // Show selected
            var selected = document.getElementById('section-' + tab);
            if (selected) selected.style.display = 'block';
            var selectedBtn = document.getElementById('tab-' + tab);
            if (selectedBtn) {
                selectedBtn.style.background = '#25D366';
                selectedBtn.style.color = 'white';
            }
        }

        function toggleConvo(id) {
            var el = document.getElementById(id);
            el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }

        flatpickr("#filterDate", {
            dateFormat: "d M y",
            allowInput: false,
            disableMobile: true,
            onChange: function(selectedDates, dateStr) {
                if (selectedDates.length > 0) {
                    var d = selectedDates[0];
                    var day = d.getDate();
                    var months = ["Jan","Feb","Mar","Apr","May","Jun",
                                  "Jul","Aug","Sep","Oct","Nov","Dec"];
                    var days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
                    var suffix = (day >= 11 && day <= 13) ? "th" :
                        (["th","st","nd","rd"][day % 10] || "th");
                    var formatted = day + suffix + " " + months[d.getMonth()] +
                        " " + String(d.getFullYear()).slice(2) +
                        " (" + days[d.getDay()] + ")";
                    document.getElementById("filterDate").setAttribute("data-value", formatted);
                } else {
                    document.getElementById("filterDate").setAttribute("data-value", "");
                }
                filterBookings();
            }
        });

        setInterval(function() {
            fetch('/stats')
                .then(r => r.json())
                .then(data => {
                    document.querySelectorAll('.stat-number')[0].textContent = data.messages;
                    document.querySelectorAll('.stat-number')[1].textContent = data.bookings;
                    document.querySelectorAll('.stat-number')[2].textContent = data.escalations;
                });
        }, 10000);

        function clearDate() {
            document.getElementById("filterDate")._flatpickr.clear();
            document.getElementById("filterDate").setAttribute("data-value", "");
            filterBookings();
        }

        function filterBookings() {
            var nameInput    = document.getElementById("searchName").value.toLowerCase();
            var dateFilter   = document.getElementById("filterDate").getAttribute("data-value") || "";
            var statusFilter = document.getElementById("filterStatus").value;
            var timeFilter   = document.getElementById("filterTime").value;
            var rows = document.querySelectorAll(".booking-row");
            var visibleCount = 0;

            rows.forEach(function(row) {
                var name   = row.getAttribute("data-name") || "";
                var date   = row.getAttribute("data-date") || "";
                var status = row.getAttribute("data-status") || "";
                var time   = row.getAttribute("data-time") || "";

                var nameMatch   = name.includes(nameInput);
                var dateMatch   = dateFilter === "" || date === dateFilter;
                var statusMatch = statusFilter === "" || status === statusFilter;
                var timeMatch   = timeFilter === "" || time === timeFilter;

                if (nameMatch && dateMatch && statusMatch && timeMatch) {
                    row.style.display = "";
                    visibleCount++;
                } else {
                    row.style.display = "none";
                }
            });

            document.getElementById("noResults").style.display =
                visibleCount === 0 ? "block" : "none";
        }
        </script>
        </div>
        <!-- END BOOKINGS TAB -->

        <!-- INBOX TAB -->
        <div id="section-inbox" style="display:none;">
        <div class="card">
            <h2>Conversations Inbox</h2>
            {% if conversations %}
                {% for number, messages in conversations.items() %}
                <div style="border:1px solid #eee;border-radius:8px;
                            margin-bottom:10px;overflow:hidden;">
                    <div style="background:#f4f4f4;padding:10px 14px;
                                display:flex;justify-content:space-between;
                                align-items:center;cursor:pointer;"
                         onclick="toggleConvo('convo-{{ loop.index }}')">
                        <div>
                            <b style="font-size:13px;">Customer</b>
                            <span style="font-size:11px;color:#999;margin-left:8px;">
                                {{ number }}
                            </span>
                        </div>
                        <span style="font-size:11px;color:#25D366;">
                            {{ messages|length }} messages ▼
                        </span>
                    </div>
                    <div id="convo-{{ loop.index }}" style="display:none;padding:10px;">
                        {% for msg in messages %}
                        <div class="message {{ 'ai-msg' if msg.role == 'ai' else 'customer-msg' }}"
                             style="margin:4px 0;">
                            <b>{{ 'AI' if msg.role == 'ai' else 'Customer' }}</b>
                            [{{ msg.time }}]: {{ msg.text }}
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <p style="color:#999;font-size:13px;">No conversations yet.</p>
            {% endif %}
        </div>
        </div>
        <!-- END INBOX TAB -->

        <!-- ANALYTICS TAB -->
        <div id="section-analytics" style="display:none;">

        <!-- KPI CARDS -->
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;">
            <div style="background:white;padding:14px;border-radius:10px;
                        box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;
                        border-top:4px solid #25D366;">
                <div style="font-size:26px;font-weight:bold;color:#25D366;">{{ bookings }}</div>
                <div style="font-size:11px;color:#666;margin-top:4px;">Total Bookings</div>
            </div>
            <div style="background:white;padding:14px;border-radius:10px;
                        box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;
                        border-top:4px solid #0f3460;">
                <div style="font-size:26px;font-weight:bold;color:#0f3460;">{{ messages }}</div>
                <div style="font-size:11px;color:#666;margin-top:4px;">Customers Reached</div>
            </div>
            <div style="background:white;padding:14px;border-radius:10px;
                        box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;
                        border-top:4px solid #e67e22;">
                <div style="font-size:26px;font-weight:bold;color:#e67e22;">{{ todays_bookings }}</div>
                <div style="font-size:11px;color:#666;margin-top:4px;">Today's Bookings</div>
            </div>
            <div style="background:white;padding:14px;border-radius:10px;
                        box-shadow:0 2px 4px rgba(0,0,0,0.1);text-align:center;
                        border-top:4px solid #c0392b;">
                <div style="font-size:26px;font-weight:bold;color:#c0392b;">{{ escalations }}</div>
                <div style="font-size:11px;color:#666;margin-top:4px;">Escalations</div>
            </div>
        </div>

        <!-- BOOKING CONVERSION RATE -->
        <div class="card">
            <h2>Booking Conversion Rate</h2>
            {% if messages > 0 %}
            {% set rate = (bookings / messages * 100)|round|int %}
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                <div style="font-size:32px;font-weight:bold;color:#25D366;">{{ rate }}%</div>
                <div style="font-size:12px;color:#666;">
                    {{ bookings }} out of {{ messages }} customers made a booking
                </div>
            </div>
            <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:16px;">
                <div style="background:linear-gradient(90deg,#25D366,#0f3460);
                            height:100%;width:{{ [rate,100]|min }}%;
                            border-radius:20px;transition:width 1s;">
                </div>
            </div>
            {% else %}
            <p style="color:#999;font-size:13px;">No data yet — send some messages first!</p>
            {% endif %}
        </div>

        <!-- BOOKINGS BY STATUS BAR CHART -->
        <div class="card">
            <h2>Bookings by Status</h2>
            {% set active_count = all_bookings|selectattr("status","eq","Active")|list|length %}
            {% set cancelled_count = all_bookings|selectattr("status","eq","Cancelled")|list|length %}
            {% set total_count = all_bookings|length %}

            <div style="margin-bottom:12px;">
                <div style="display:flex;justify-content:space-between;
                            font-size:12px;margin-bottom:4px;">
                    <span style="color:#25D366;font-weight:bold;">Active</span>
                    <span>{{ active_count }}</span>
                </div>
                <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:20px;">
                    {% if total_count > 0 %}
                    <div style="background:#25D366;height:100%;
                                width:{{ (active_count/total_count*100)|round|int }}%;
                                border-radius:20px;">
                    </div>
                    {% endif %}
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;
                            font-size:12px;margin-bottom:4px;">
                    <span style="color:#c0392b;font-weight:bold;">Cancelled</span>
                    <span>{{ cancelled_count }}</span>
                </div>
                <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:20px;">
                    {% if total_count > 0 %}
                    <div style="background:#c0392b;height:100%;
                                width:{{ (cancelled_count/total_count*100)|round|int }}%;
                                border-radius:20px;">
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>

        <!-- BOOKINGS BY TIME SLOT -->
        <div class="card">
            <h2>Popular Booking Times</h2>
            {% set time_slots = {} %}
            {% for b in all_bookings %}
                {% if b.time %}
                    {% if b.time in time_slots %}
                        {% set _ = time_slots.update({b.time: time_slots[b.time] + 1}) %}
                    {% else %}
                        {% set _ = time_slots.update({b.time: 1}) %}
                    {% endif %}
                {% endif %}
            {% endfor %}
            {% if time_slots %}
                {% set max_val = time_slots.values()|max %}
                {% for time, count in time_slots.items() %}
                <div style="margin-bottom:10px;">
                    <div style="display:flex;justify-content:space-between;
                                font-size:12px;margin-bottom:4px;">
                        <span style="font-weight:bold;">{{ time }}</span>
                        <span>{{ count }} booking{{ 's' if count > 1 else '' }}</span>
                    </div>
                    <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:16px;">
                        <div style="background:linear-gradient(90deg,#25D366,#0f3460);
                                    height:100%;
                                    width:{{ (count/max_val*100)|round|int }}%;
                                    border-radius:20px;">
                        </div>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <p style="color:#999;font-size:13px;">No bookings yet.</p>
            {% endif %}
        </div>

        <!-- AI PERFORMANCE -->
        <div class="card">
            <h2>AI Performance</h2>
            <div style="display:flex;flex-direction:column;gap:10px;">
                <div style="display:flex;justify-content:space-between;
                            padding:10px;background:#e8f8f0;border-radius:8px;">
                    <span style="font-size:13px;">Messages Handled by AI</span>
                    <span style="font-weight:bold;color:#25D366;">100%</span>
                </div>
                <div style="display:flex;justify-content:space-between;
                            padding:10px;background:#f4f4f4;border-radius:8px;">
                    <span style="font-size:13px;">Escalations to Owner</span>
                    <span style="font-weight:bold;color:#c0392b;">{{ escalations }}</span>
                </div>
                <div style="display:flex;justify-content:space-between;
                            padding:10px;background:#eaf0ff;border-radius:8px;">
                    <span style="font-size:13px;">Languages Supported</span>
                    <span style="font-weight:bold;color:#0f3460;">Arabic + English</span>
                </div>
                <div style="display:flex;justify-content:space-between;
                            padding:10px;background:#fff4e6;border-radius:8px;">
                    <span style="font-size:13px;">Availability</span>
                    <span style="font-weight:bold;color:#e67e22;">24/7</span>
                </div>
            </div>
        </div>

        </div>
        <!-- END ANALYTICS TAB -->

        <!-- SETTINGS TAB -->
        <div id="section-settings" style="display:none;">
        <div class="card">
            <h2>Settings</h2>
            <div style="display:flex;flex-direction:column;gap:12px;">
                <div>
                    <label style="font-size:12px;color:#666;font-weight:bold;">
                        Business Name
                    </label>
                    <input type="text" value="Tasty Bites Restaurant"
                        style="width:100%;padding:8px 12px;border:1px solid #ddd;
                               border-radius:8px;font-size:13px;margin-top:4px;">
                </div>
                <div>
                    <label style="font-size:12px;color:#666;font-weight:bold;">
                        Opening Hours
                    </label>
                    <input type="text" value="12pm to 11pm daily"
                        style="width:100%;padding:8px 12px;border:1px solid #ddd;
                               border-radius:8px;font-size:13px;margin-top:4px;">
                </div>
                <div>
                    <label style="font-size:12px;color:#666;font-weight:bold;">
                        Location
                    </label>
                    <input type="text" value="Khalidiyah Mall area, Abu Dhabi"
                        style="width:100%;padding:8px 12px;border:1px solid #ddd;
                               border-radius:8px;font-size:13px;margin-top:4px;">
                </div>
                <div>
                    <label style="font-size:12px;color:#666;font-weight:bold;">
                        Owner WhatsApp
                    </label>
                    <input type="text" placeholder="+971XXXXXXXXX"
                        style="width:100%;padding:8px 12px;border:1px solid #ddd;
                               border-radius:8px;font-size:13px;margin-top:4px;">
                </div>
                <div>
                    <label style="font-size:12px;color:#666;font-weight:bold;">
                        AI Tone
                    </label>
                    <select style="width:100%;padding:8px 12px;border:1px solid #ddd;
                                   border-radius:8px;font-size:13px;margin-top:4px;">
                        <option>Friendly and warm</option>
                        <option>Professional and formal</option>
                        <option>Fun and casual</option>
                    </select>
                </div>
                <button style="padding:12px;background:#25D366;color:white;
                               border:none;border-radius:8px;font-size:14px;
                               font-weight:bold;cursor:pointer;">
                    Save Settings
                </button>
            </div>
        </div>
        </div>
        <!-- END SETTINGS TAB -->

        <!-- MINI BOOKINGS IN OVERVIEW -->
        <div class="card">
            <h2>Recent Bookings</h2>
            {% if all_bookings %}
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
                <tr>
                    <th style="padding:6px;background:#f4f4f4;text-align:left;">Name</th>
                    <th style="padding:6px;background:#f4f4f4;text-align:left;">Date</th>
                    <th style="padding:6px;background:#f4f4f4;text-align:left;">Time</th>
                    <th style="padding:6px;background:#f4f4f4;text-align:left;">Status</th>
                </tr>
                {% for b in all_bookings|reverse|list %}
                {% if loop.index <= 3 %}
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:6px;">{{ b.name }}</td>
                    <td style="padding:6px;">{{ b.date }}</td>
                    <td style="padding:6px;">{{ b.time }}</td>
                    <td style="padding:6px;">
                        {% if b.status == "Active" %}
                            <span style="background:#e8f8f0;color:#25D366;
                                padding:2px 6px;border-radius:8px;
                                font-weight:bold;font-size:10px;">Active</span>
                        {% else %}
                            <span style="background:#fdecea;color:#c0392b;
                                padding:2px 6px;border-radius:8px;
                                font-weight:bold;font-size:10px;">Cancelled</span>
                        {% endif %}
                    </td>
                </tr>
                {% endif %}
                {% endfor %}
            </table>
            <p style="font-size:11px;color:#999;margin-top:8px;text-align:right;">
                <a href="#" onclick="showTab('bookings')"
                   style="color:#25D366;text-decoration:none;font-weight:bold;">
                   View all bookings →
                </a>
            </p>
            {% else %}
                <p style="color:#999;font-size:13px;">No bookings yet.</p>
            {% endif %}
        </div>

        <!-- MINI ANALYTICS IN OVERVIEW -->
        <div class="card">
            <h2>Quick Analytics</h2>

            <!-- Conversion Rate Bar -->
            <div style="margin-bottom:14px;">
                <div style="display:flex;justify-content:space-between;
                            font-size:12px;margin-bottom:4px;">
                    <span style="font-weight:bold;">Booking Conversion Rate</span>
                    {% if messages > 0 %}
                    <span style="color:#25D366;font-weight:bold;">
                        {{ (bookings/messages*100)|round|int }}%
                    </span>
                    {% else %}
                    <span style="color:#999;">No data</span>
                    {% endif %}
                </div>
                <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:14px;">
                    {% if messages > 0 %}
                    <div style="background:linear-gradient(90deg,#25D366,#0f3460);
                                height:100%;
                                width:{{ [(bookings/messages*100)|round|int, 100]|min }}%;
                                border-radius:20px;">
                    </div>
                    {% endif %}
                </div>
            </div>

            <!-- Active vs Cancelled Bar -->
            {% set active_count = all_bookings|selectattr("status","eq","Active")|list|length %}
            {% set cancelled_count = all_bookings|selectattr("status","eq","Cancelled")|list|length %}
            {% set total_count = all_bookings|length %}

            <div style="margin-bottom:14px;">
                <div style="display:flex;justify-content:space-between;
                            font-size:12px;margin-bottom:4px;">
                    <span style="font-weight:bold;color:#25D366;">Active Bookings</span>
                    <span>{{ active_count }}</span>
                </div>
                <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:14px;">
                    {% if total_count > 0 %}
                    <div style="background:#25D366;height:100%;
                                width:{{ (active_count/total_count*100)|round|int }}%;
                                border-radius:20px;">
                    </div>
                    {% endif %}
                </div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="display:flex;justify-content:space-between;
                            font-size:12px;margin-bottom:4px;">
                    <span style="font-weight:bold;color:#c0392b;">Cancelled Bookings</span>
                    <span>{{ cancelled_count }}</span>
                </div>
                <div style="background:#f4f4f4;border-radius:20px;overflow:hidden;height:14px;">
                    {% if total_count > 0 %}
                    <div style="background:#c0392b;height:100%;
                                width:{{ (cancelled_count/total_count*100)|round|int }}%;
                                border-radius:20px;">
                    </div>
                    {% endif %}
                </div>
            </div>

            <p style="font-size:11px;color:#999;margin-top:8px;text-align:right;">
                <a href="#" onclick="showTab('analytics')"
                   style="color:#25D366;text-decoration:none;font-weight:bold;">
                   Full analytics →
                </a>
            </p>
        </div>

        <!-- TIMELINE VIEW -->
        <div class="card">
            <h2>Booking Timeline</h2>
            {% if all_bookings %}
            <div style="position:relative;padding-left:24px;">
                <!-- Vertical line -->
                <div style="position:absolute;left:8px;top:0;bottom:0;
                            width:2px;background:#e0e0e0;"></div>
                {% for b in all_bookings|reverse %}
                {% if loop.index <= 5 %}
                <div style="position:relative;margin-bottom:16px;">
                    <!-- Dot -->
                    <div style="position:absolute;left:-20px;top:4px;
                                width:12px;height:12px;border-radius:50%;
                                background:{{ '#25D366' if b.status == 'Active' else '#c0392b' }};
                                border:2px solid white;
                                box-shadow:0 0 0 2px {{ '#25D366' if b.status == 'Active' else '#c0392b' }};">
                    </div>
                    <!-- Content -->
                    <div style="background:#f9f9f9;border-radius:8px;
                                padding:10px 12px;border-left:3px solid
                                {{ '#25D366' if b.status == 'Active' else '#c0392b' }};">
                        <div style="display:flex;justify-content:space-between;
                                    align-items:center;">
                            <b style="font-size:13px;">{{ b.name }}</b>
                            <span style="font-size:10px;
                                background:{{ '#e8f8f0' if b.status == 'Active' else '#fdecea' }};
                                color:{{ '#25D366' if b.status == 'Active' else '#c0392b' }};
                                padding:2px 6px;border-radius:6px;font-weight:bold;">
                                {{ b.status }}
                            </span>
                        </div>
                        <div style="font-size:11px;color:#666;margin-top:4px;">
                            📅 {{ b.date }} &nbsp;|&nbsp; 🕐 {{ b.time }}
                            &nbsp;|&nbsp; 👥 {{ b.party_size }} people
                        </div>
                    </div>
                </div>
                {% endif %}
                {% endfor %}
            </div>
            {% if all_bookings|length > 5 %}
            <p style="font-size:11px;color:#999;text-align:right;margin-top:4px;">
                <a href="#" onclick="showTab('bookings')"
                   style="color:#25D366;text-decoration:none;font-weight:bold;">
                   View all {{ all_bookings|length }} bookings →
                </a>
            </p>
            {% endif %}
            {% else %}
                <p style="color:#999;font-size:13px;">No bookings yet.</p>
            {% endif %}
        </div>

        <!-- PIE CHART — Active vs Cancelled -->
        <div class="card">
            <h2>Booking Status</h2>
            <div style="display:flex;align-items:center;gap:20px;">
                <div style="width:200px;height:200px;">
                    <canvas id="statusPieChart"></canvas>
                </div>
                <div style="flex:1;">
                    {% set active_count = all_bookings|selectattr("status","eq","Active")|list|length %}
                    {% set cancelled_count = all_bookings|selectattr("status","eq","Cancelled")|list|length %}
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                        <div style="width:12px;height:12px;border-radius:50%;
                                    background:#25D366;flex-shrink:0;"></div>
                        <span style="font-size:13px;">Active</span>
                        <b style="margin-left:auto;color:#25D366;">{{ active_count }}</b>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <div style="width:12px;height:12px;border-radius:50%;
                                    background:#c0392b;flex-shrink:0;"></div>
                        <span style="font-size:13px;">Cancelled</span>
                        <b style="margin-left:auto;color:#c0392b;">{{ cancelled_count }}</b>
                    </div>
                    <div style="margin-top:12px;padding-top:12px;border-top:1px solid #eee;">
                        <span style="font-size:11px;color:#666;">Total Bookings</span>
                        <b style="float:right;">{{ all_bookings|length }}</b>
                    </div>
                </div>
            </div>
        </div>

        <!-- RECENT CONVERSATIONS (Overview) -->
        <div id="section-overview-convos">
        <div class="card">
            <h2>Recent Conversations</h2>
            {% if conversations %}
                {% for number, messages in conversations.items() %}
                    <div class="convo-header">Customer: ...{{ number[-4:] }}</div>
                    {% for msg in messages[-4:] %}
                        <div class="message {{ 'ai-msg' if msg.role == 'ai' else 'customer-msg' }}">
                            <b>{{ 'AI' if msg.role == 'ai' else 'Customer' }}</b>
                            [{{ msg.time }}]: {{ msg.text[:100] }}
                        </div>
                    {% endfor %}
                {% endfor %}
            {% else %}
                <p style="color:#999;font-size:13px;">No conversations yet.</p>
            {% endif %}
        </div>
        </div>
    </body>
    </html>
    """
    all_bookings = load_all_bookings()
    return render_template_string(
        html,
        messages=stats["messages"],
        bookings=stats["bookings"],
        escalations=stats["escalations"],
        conversations=stats["conversations"],
        all_bookings=all_bookings,
        todays_bookings=count_todays_bookings()
    )

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  ZapReply is starting...")
    print("  Powered by Groq AI (Free)")
    print("  Dashboard: http://localhost:5000")
    print("  Webhook:   http://localhost:5000/whatsapp")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)