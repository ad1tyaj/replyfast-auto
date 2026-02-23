import json
from datetime import datetime
import requests

from flask import Flask, request, jsonify

from config import (
    PORT,
    META_API_TOKEN,
    META_PHONE_ID,
    SHEET_KEY,
)
from meta_whatsapp import meta_api

app = Flask(__name__)

# Simple in-memory storage (for testing without Redis)
user_states = {}

# =========================
# Integrations
# =========================

def send_whatsapp_message(wa_id, message_body, buttons=None):
    """
    Sends WhatsApp message using Meta WhatsApp Business API.
    'buttons' must be a list of button titles or None.
    """
    try:
        if buttons and len(buttons) > 0:
            if len(buttons) <= 3:
                # Use interactive buttons for 3 or fewer options
                result = meta_api.send_interactive_button_message(wa_id, message_body, buttons)
            else:
                # Use interactive list for more than 3 options
                result = meta_api.send_interactive_list_message(wa_id, message_body, "Please select:", buttons)
        else:
            # Send simple text message
            result = meta_api.send_text_message(wa_id, message_body)
        
        if result['success']:
            print(f"✅ [WHATSAPP MESSAGE SENT] To: {wa_id}")
            print(f"   Message: {message_body}")
            if buttons:
                print(f"   Buttons: {', '.join(buttons)}")
        else:
            print(f"❌ [WHATSAPP MESSAGE FAILED] To: {wa_id}")
            print(f"   Error: {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        print(f"❌ [WHATSAPP MESSAGE ERROR] To: {wa_id}")
        print(f"   Exception: {str(e)}")
        return {"success": False, "error": str(e)}


def log_lead_to_sheet(lead_data):
    """Logs the lead to Google Sheets using webhook or direct API."""
    try:
        if not SHEET_KEY:
            print(f"📊 [LEAD LOGGED LOCALLY]")
            print(f"   {json.dumps(lead_data, indent=2)}")
            return {"success": True, "method": "local_only"}
        
        webhook_url = f"https://script.google.com/macros/s/{SHEET_KEY}/exec"
        response = requests.post(webhook_url, json=lead_data, timeout=10)
        
        if response.status_code == 200:
            print(f"✅ [LEAD LOGGED TO SHEETS]")
            return {"success": True, "response": response.text}
        else:
            print(f"❌ [SHEETS LOGGING FAILED] - {response.status_code}")
            print(f"📊 [LEAD LOGGED LOCALLY AS FALLBACK]")
            print(f"   {json.dumps(lead_data, indent=2)}")
            return {"success": False, "fallback": "local"}
            
    except Exception as e:
        print(f"❌ [SHEETS ERROR] - {str(e)}")
        print(f"📊 [LEAD LOGGED LOCALLY AS FALLBACK]")
        print(f"   {json.dumps(lead_data, indent=2)}")
        return {"success": False, "fallback": "local"}


# =========================
# State Management (In-Memory)
# =========================

def get_user_state(wa_id):
    """Retrieve user state from memory."""
    return user_states.get(wa_id, {"q_status": 0, "answers": {}})

def save_user_state(wa_id, state):
    """Save user state to memory."""
    user_states[wa_id] = state

def reset_user_state(wa_id):
    """Reset user state."""
    user_states.pop(wa_id, None)

# =========================
# Question Helpers
# =========================

def send_q1(wa_id):
    text = "🚗 Welcome to ReplyFast Auto! \n\nQ1: Are you looking for a New or Used Car?"
    buttons = ["New", "Used"]
    send_whatsapp_message(wa_id, text, buttons=buttons)

def send_q2(wa_id):
    text = "Q2: What's your preferred model or budget?\n\nPlease tell me the car model you're interested in or your budget range."
    send_whatsapp_message(wa_id, text)

def send_q3(wa_id):
    text = "Q3: What is your urgency to purchase?"
    buttons = ["HOT (7 days)", "WARM (1 month)", "COLD (6+ months)"]
    send_whatsapp_message(wa_id, text, buttons=buttons)

def send_q4(wa_id):
    text = "Q4: Would you like to book a test drive?"
    buttons = ["Book Now", "More Details"]
    send_whatsapp_message(wa_id, text, buttons=buttons)

def send_q5(wa_id):
    text = "Q5: Please share your contact details:\n\nFormat: Your Name, Phone Number, Preferred call time\nExample: John Doe, 9876543210, 3 PM today"
    send_whatsapp_message(wa_id, text)

def complete_lead(wa_id, state):
    answers = state.get("answers", {})
    timestamp = datetime.utcnow().isoformat()

    lead_data = {
        "wa_id": wa_id,
        "q1_car_type": answers.get("q1"),
        "q2_model_budget": answers.get("q2"),
        "q3_urgency": answers.get("q3"),
        "q4_test_drive": answers.get("q4"),
        "q5_contact_details": answers.get("q5"),
        "lead_score": answers.get("lead_score"),
        "timestamp": timestamp,
    }

    log_lead_to_sheet(lead_data)
    reset_user_state(wa_id)

    send_whatsapp_message(
        wa_id,
        "🎉 Thank you! Your information has been received.\n\n"
        "Our sales team will contact you shortly at your preferred time.\n\n"
        "Have a great day! 🚗✨"
    )

def extract_message_content(data):
    """Extract message content from Meta webhook formats"""
    # Try new format first
    entries = data.get("entry", [])
    if entries:
        changes = entries[0].get("changes", [])
        if changes:
            messages = changes[0].get("value", {}).get("messages", [])
            if messages:
                message = messages[0]
                wa_id = message.get("from")
                
                text_content = ""
                if "text" in message:
                    text_content = message["text"].get("body", "")
                elif "interactive" in message:
                    interactive = message["interactive"]
                    if interactive["type"] == "button_reply":
                        text_content = interactive["button_reply"]["title"]
                    elif interactive["type"] == "list_reply":
                        text_content = interactive["list_reply"]["title"]
                
                return wa_id, text_content.strip()
    
    # Try legacy format
    messages = data.get("messages", [])
    if messages:
        message = messages[0]
        wa_id = message.get("from")
        text_obj = message.get("text") or {}
        text_content = text_obj.get("body", "").strip()
        return wa_id, text_content
    
    return None, None

# =========================
# Webhook Route
# =========================

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    """Webhook endpoint for Meta WhatsApp Business API"""
    if request.method == "GET":
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        expected_token = "your_verify_token_here"
        
        if verify_token == expected_token:
            return challenge
        else:
            return "Forbidden", 403
    
    # POST request - handle incoming messages
    data = request.get_json(silent=True) or {}
    wa_id, incoming_text = extract_message_content(data)
    
    if not wa_id or incoming_text is None:
        return "", 200

    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})

    # Q1 - Start
    if q_status == 0:
        state["q_status"] = 1
        save_user_state(wa_id, state)
        send_q1(wa_id)
        return jsonify({"status": "ok", "step": "sent_q1"})

    # Q1 Answer
    if q_status == 1:
        if incoming_text.lower() not in ["new", "used"]:
            send_q1(wa_id)
            return jsonify({"status": "ok", "step": "invalid_q1"})
        
        answers["q1"] = incoming_text
        state["answers"] = answers
        state["q_status"] = 2
        save_user_state(wa_id, state)
        send_q2(wa_id)
        return jsonify({"status": "ok", "step": "sent_q2"})

    # Q2 Answer
    if q_status == 2:
        if not incoming_text:
            send_q2(wa_id)
            return jsonify({"status": "ok", "step": "repeat_q2"})
        
        answers["q2"] = incoming_text
        state["answers"] = answers
        state["q_status"] = 3
        save_user_state(wa_id, state)
        send_q3(wa_id)
        return jsonify({"status": "ok", "step": "sent_q3"})

    # Q3 Answer
    if q_status == 3:
        normalized = incoming_text.lower()
        if "hot" in normalized:
            lead_score = "HOT"
        elif "warm" in normalized:
            lead_score = "WARM"
        elif "cold" in normalized:
            lead_score = "COLD"
        else:
            send_q3(wa_id)
            return jsonify({"status": "ok", "step": "invalid_q3"})

        answers["q3"] = incoming_text
        answers["lead_score"] = lead_score
        state["answers"] = answers
        state["q_status"] = 4
        save_user_state(wa_id, state)
        send_q4(wa_id)
        return jsonify({"status": "ok", "step": "sent_q4"})

    # Q4 Answer
    if q_status == 4:
        if incoming_text.lower() not in ["book now", "more details"]:
            send_q4(wa_id)
            return jsonify({"status": "ok", "step": "invalid_q4"})

        answers["q4"] = incoming_text
        state["answers"] = answers
        state["q_status"] = 5
        save_user_state(wa_id, state)
        send_q5(wa_id)
        return jsonify({"status": "ok", "step": "sent_q5"})

    # Q5 Answer - Complete
    if q_status == 5:
        if not incoming_text:
            send_q5(wa_id)
            return jsonify({"status": "ok", "step": "repeat_q5"})

        answers["q5"] = incoming_text
        state["answers"] = answers
        save_user_state(wa_id, state)
        complete_lead(wa_id, state)
        return jsonify({"status": "ok", "step": "completed_lead"})

    # Reset if confused
    reset_user_state(wa_id)
    new_state = {"q_status": 1, "answers": {}}
    save_user_state(wa_id, new_state)
    send_q1(wa_id)
    return jsonify({"status": "ok", "step": "reset_and_sent_q1"})

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.utcnow().isoformat(),
        "storage": "in-memory",
        "active_users": len(user_states)
    })

if __name__ == "__main__":
    print(f"🚀 ReplyFast Auto (No Redis Version) starting on port {PORT}")
    print(f"📱 Meta WhatsApp API: {'✅ Configured' if META_API_TOKEN and META_PHONE_ID else '❌ Not Configured'}")
    print(f"📊 Google Sheets: {'✅ Configured' if SHEET_KEY else '❌ Not Configured'}")
    print(f"💾 Storage: In-Memory (for testing)")
    
    app.run(host="0.0.0.0", port=PORT, debug=True)