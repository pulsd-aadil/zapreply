# ============================================================
# ZapReply - AI WhatsApp Assistant
# Powered by Gemini AI (Free)
# ============================================================

from flask import Flask, request, render_template_string
import google.generativeai as genai
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
import os
from datetime import datetime

# Load secret keys from .env
load_dotenv()

# Create web server
app = Flask(__name__)

# Connect to Gemini AI
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ============================================================
# BUSINESS PROMPT - The AI personality for your demo business
# This is what makes the AI sound like a real business
# ============================================================
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

BOOKING FLOW - follow this order every time:
Step 1 - Ask for customer name
Step 2 - Ask for date
Step 3 - Ask for time
Step 4 - Ask for number of people
Step 5 - Confirm everything back with a summary
Step 6 - End with: Your booking is confirmed! See you then!

IF YOU CANNOT HELP:
- Say: I will pass this to our manager who will reply shortly
- Never invent prices or information you are not sure about
"""

# ============================================================
# MEMORY - Stores conversation per customer number
# So AI remembers the full conversation context
# ============================================================
conversation_history = {}

# ============================================================
# STATS - Tracks daily activity
# ============================================================
stats = {
    "messages": 0,
    "bookings": 0,
    "escalations": 0,
    "conversations": {}
}

# ============================================================
# ESCALATION - Detects angry or urgent messages
# ============================================================
def needs_escalation(message):
    triggers = [
        "angry", "terrible", "horrible", "refund", "manager",
        "unacceptable", "worst", "complaint", "disgusting",
        "غاضب", "مدير", "شكوى", "استرداد", "سيء", "فظيع"
    ]
    message_lower = message.lower()
    for trigger in triggers:
        if trigger in message_lower:
            return True
    return False

# ============================================================
# ALERT OWNER - Sends WhatsApp to business owner
# ============================================================
def alert_owner(customer_number, customer_message):
    try:
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        alert = (
            f"ESCALATION ALERT\n"
            f"Customer: {customer_number}\n"
            f"Message: {customer_message}\n"
            f"Please reply to them directly."
        )
        client.messages.create(
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{os.getenv('OWNER_WHATSAPP')}",
            body=alert
        )
        print("Owner alerted successfully")
    except Exception as e:
        print(f"Could not alert owner: {e}")

# ============================================================
# MAIN WEBHOOK
# Twilio calls this every time a WhatsApp message arrives
# ============================================================
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    # Get message from Twilio
    customer_message = request.form.get("Body", "").strip()
    customer_number = request.form.get("From", "")
    timestamp = datetime.now().strftime("%H:%M")

    print(f"\n[{timestamp}] FROM: {customer_number}")
    print(f"MESSAGE: {customer_message}")

    # Handle empty messages
    if not customer_message:
        customer_message = "Hello"

    # Update stats
    stats["messages"] += 1

    # Track booking keywords
    booking_keywords = ["book", "reserve", "reservation", "table", "حجز", "احجز"]
    if any(kw in customer_message.lower() for kw in booking_keywords):
        stats["bookings"] += 1

    # Store conversation for dashboard
    if customer_number not in stats["conversations"]:
        stats["conversations"][customer_number] = []
    stats["conversations"][customer_number].append({
        "role": "customer",
        "text": customer_message,
        "time": timestamp
    })

    # Start memory for new customers
    if customer_number not in conversation_history:
        conversation_history[customer_number] = []

    # Add message to memory
    conversation_history[customer_number].append({
        "role": "user",
        "parts": [customer_message]
    })

    # Keep only last 10 messages to save API cost
    if len(conversation_history[customer_number]) > 10:
        conversation_history[customer_number] = \
            conversation_history[customer_number][-10:]

    # Check if escalation needed
    if needs_escalation(customer_message):
        stats["escalations"] += 1
        alert_owner(customer_number, customer_message)

    # Send to Gemini AI and get reply
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=BUSINESS_PROMPT
        )
        chat = model.start_chat(history=conversation_history[customer_number][:-1])
        response = chat.send_message(customer_message)
        ai_reply = response.text

        # Save AI reply to memory
        conversation_history[customer_number].append({
            "role": "model",
            "parts": [ai_reply]
        })

        # Save to dashboard conversations
        stats["conversations"][customer_number].append({
            "role": "ai",
            "text": ai_reply,
            "time": timestamp
        })

    except Exception as e:
        print(f"Gemini error: {e}")
        ai_reply = "Thank you for your message! We will reply shortly."

    print(f"AI REPLY: {ai_reply[:80]}...")

    # Send reply back via Twilio
    twilio_response = MessagingResponse()
    twilio_response.message(ai_reply)
    return str(twilio_response)

# ============================================================
# DASHBOARD - Web page showing all activity
# Open this in your browser to see everything happening
# ============================================================
@app.route("/")
def dashboard():
    dashboard_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>ZapReply Dashboard</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: Arial, sans-serif; background: #f0f0f0; padding: 16px; }
            .header { background: #25D366; color: white; padding: 20px; 
                      border-radius: 12px; margin-bottom: 16px; }
            .header h1 { font-size: 22px; }
            .header p { font-size: 13px; opacity: 0.9; margin-top: 4px; }
            .stats { display: grid; grid-template-columns: repeat(3, 1fr); 
                     gap: 10px; margin-bottom: 16px; }
            .stat-card { background: white; padding: 16px; border-radius: 10px; 
                         text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .stat-number { font-size: 32px; font-weight: bold; color: #25D366; }
            .stat-label { font-size: 11px; color: #666; margin-top: 4px; }
            .card { background: white; padding: 16px; border-radius: 10px; 
                    margin-bottom: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .card h2 { font-size: 15px; color: #333; margin-bottom: 12px; 
                       border-bottom: 2px solid #25D366; padding-bottom: 8px; }
            .message { padding: 8px 12px; border-radius: 8px; margin: 6px 0; 
                       font-size: 13px; }
            .customer-msg { background: #f0f0f0; border-left: 3px solid #666; }
            .ai-msg { background: #e8f8f0; border-left: 3px solid #25D366; }
            .convo-header { font-size: 11px; color: #999; margin-top: 10px; 
                            margin-bottom: 4px; font-weight: bold; }
            .status { background: #e8f8f0; color: #25D366; padding: 8px 12px; 
                      border-radius: 8px; font-size: 13px; font-weight: bold; }
        </style>
        <meta http-equiv="refresh" content="10">
    </head>
    <body>
        <div class="header">
            <h1>ZapReply Dashboard</h1>
            <p>Tasty Bites Demo — AI Working 24/7</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{{ messages }}</div>
                <div class="stat-label">Messages</div>
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
            <div class="status">AI is online and replying</div>
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
                <p style="color:#999; font-size:13px;">
                    No conversations yet. Send a WhatsApp message to start!
                </p>
            {% endif %}
        </div>
    </body>
    </html>
    """
    return render_template_string(
        dashboard_html,
        messages=stats["messages"],
        bookings=stats["bookings"],
        escalations=stats["escalations"],
        conversations=stats["conversations"]
    )

# ============================================================
# START SERVER
# ============================================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("  ZapReply is starting...")
    print("  Dashboard: http://localhost:5000")
    print("  Webhook:   http://localhost:5000/whatsapp")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)