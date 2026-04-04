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
from datetime import datetime
from bookings import save_booking, load_all_bookings, get_todays_bookings

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
Step 3 - Confirm the cancellation with: Your booking has been cancelled. We hope to see you another time!

CANCELLATION FLOW - when customer wants to cancel:
Step 1 - Ask for their name
Step 2 - Ask for the date and time of their booking
Step 3 - Confirm the cancellation with: Your booking has been cancelled. We hope to see you another time!

IF YOU CANNOT HELP:
- Say: I will pass this to our manager who will reply shortly
- Never invent information you are not sure about
"""

conversation_history = {}

stats = {
    "messages": 0,
    "bookings": 0,
    "escalations": 0,
    "conversations": {}
}

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

    if customer_number not in stats["conversations"] or len(stats["conversations"].get(customer_number, [])) == 0:
        stats["messages"] += 1

    booking_keywords = ["i want to book", "book a table", "make a reservation", "reserve a table", "احجز طاولة", "حجز طاولة"]

    


    if customer_number not in stats["conversations"]:
        stats["conversations"][customer_number] = []
    stats["conversations"][customer_number].append({
        "role": "customer", "text": customer_message, "time": timestamp
    })

    if customer_number not in conversation_history:
        conversation_history[customer_number] = []

    conversation_history[customer_number].append({
        "role": "user", "content": customer_message
    })

    if len(conversation_history[customer_number]) > 10:
        conversation_history[customer_number] = \
            conversation_history[customer_number][-10:]

    if needs_escalation(customer_message):
        stats["escalations"] += 1
        alert_owner(customer_number, customer_message)

    try:
        messages = [{"role": "system", "content": BUSINESS_PROMPT}]
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

        # Booking counter goes up when AI confirms booking
        confirmation_keywords = [
            "booking is confirmed", "reservation is confirmed",
            "see you then", "booking confirmed",
            "تم تأكيد", "حجزك مؤكد", "نراكم قريباً"
        ]
        if any(kw.lower() in ai_reply.lower() for kw in confirmation_keywords):
            stats["bookings"] += 1
            save_booking(
                name="Guest",
                phone=customer_number,
                date="",
                time="",
                party_size=""
            )
            print("Booking confirmed and saved!")

        # Booking counter goes down only when AI confirms cancellation
        cancellation_confirmed = [
            "cancellation is confirmed", "booking has been cancelled",
            "successfully cancelled", "booking cancelled",
            "reservation cancelled", "تم الالغاء", "تم إلغاء الحجز"
        ]
        if any(kw.lower() in ai_reply.lower() for kw in cancellation_confirmed):
            if stats["bookings"] > 0:
                stats["bookings"] -= 1
            print("Booking cancelled and counter updated!")

    except Exception as e:
        print(f"Groq error: {e}")
        ai_reply = "Thank you for your message! We will reply shortly."

    print(f"AI REPLY: {ai_reply[:80]}...")

    twilio_response = MessagingResponse()
    twilio_response.message(ai_reply)
    return str(twilio_response)

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
            .stats { display:grid; grid-template-columns:repeat(3,1fr);
                     gap:10px; margin-bottom:16px; }
            .stat-card { background:white; padding:16px; border-radius:10px;
                         text-align:center; box-shadow:0 2px 4px rgba(0,0,0,0.1); }
            .stat-number { font-size:32px; font-weight:bold; color:#25D366; }
            .stat-label { font-size:11px; color:#666; margin-top:4px; }
            .card { background:white; padding:16px; border-radius:10px;
                    margin-bottom:12px; box-shadow:0 2px 4px rgba(0,0,0,0.1); }
            .card h2 { font-size:15px; color:#333; margin-bottom:12px;
                       border-bottom:2px solid #25D366; padding-bottom:8px; }
            .message { padding:8px 12px; border-radius:8px; margin:6px 0; font-size:13px; }
            .customer-msg { background:#f0f0f0; border-left:3px solid #666; }
            .ai-msg { background:#e8f8f0; border-left:3px solid #25D366; }
            .convo-header { font-size:11px; color:#999; margin-top:10px;
                            margin-bottom:4px; font-weight:bold; }
            .status { background:#e8f8f0; color:#25D366; padding:8px 12px;
                      border-radius:8px; font-size:13px; font-weight:bold; }
        </style>
        <meta http-equiv="refresh" content="10">
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
                <div class="stat-label">Bookings</div>
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
            {% if all_bookings %}
                {% for b in all_bookings %}
                <div class="message customer-msg">
                    <b>{{ b.name }}</b> — {{ b.date }} at {{ b.time }}
                    ({{ b.party_size }} people) | {{ b.created_at }}
                </div>
                {% endfor %}
            {% else %}
                <p style="color:#999;font-size:13px;">No bookings yet.</p>
            {% endif %}
        </div>
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
        all_bookings=all_bookings
    )

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  ZapReply is starting...")
    print("  Powered by Groq AI (Free)")
    print("  Dashboard: http://localhost:5000")
    print("  Webhook:   http://localhost:5000/whatsapp")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)