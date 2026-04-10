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
    </head>
    <body>
        <div class="header">
            <h1>ZapReply Dashboard</h1>
            <p>Tasty Bites — Powered by Groq AI</p>
        </div>
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