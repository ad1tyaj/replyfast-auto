# A/B Test Implementation Guide

## 🎯 What We're Testing

**FLOW A (Current):** New/Used → Model → Budget → Timeline → Contact  
**FLOW B (Optimized):** Timeline → New/Used → Budget → Model → Contact

**Hypothesis:** Starting with "Timeline" will identify HOT leads faster and increase conversion rate by 15-25%.

---

## 📊 Metrics We'll Track

### **Primary Metric (Winner Determined By):**
- **HOT Lead Percentage:** % of completed conversations that are HOT leads
  - Flow A baseline: ~25-30%
  - Flow B target: 35-45%

### **Secondary Metrics:**
- **Completion Rate:** % who complete all 5 questions
- **Time to Complete:** Average seconds to finish
- **Dropout Rate:** Where people quit
- **Contact Capture Rate:** % who provide phone number

### **Success Criteria:**
- Need 100 completed conversations per flow (200 total)
- 95% statistical confidence
- Flow B must beat Flow A by >10% on primary metric

---

## 🔧 Implementation Steps

### **Step 1: Add A/B Test Configuration to app.py**

Add this at the top of `app.py`:

```python
# Import A/B test configuration
from ab_test_flows import (
    AB_TEST_CONFIG,
    assign_user_to_flow,
    get_flow_for_user,
    calculate_lead_score,
    determine_lead_category,
    track_ab_test_metric
)

# Enable/disable A/B test
AB_TEST_ENABLED = True  # Set to False to use only Flow B
```

---

### **Step 2: Modify User State to Include Flow Assignment**

Update the `get_user_state()` function:

```python
def get_user_state(wa_id):
    """
    Retrieve user state from Redis or memory fallback.
    NOW includes flow assignment for A/B testing
    """
    default_state = {
        "q_status": 0,
        "answers": {},
        "flow": None,  # NEW: Track which flow user is in
        "flow_start_time": None,  # NEW: Track start time
        "ab_test_metrics": {}  # NEW: Track metrics
    }
    
    if REDIS_AVAILABLE:
        try:
            raw = r.get(wa_id)
            if not raw:
                # NEW USER: Assign to A/B test flow
                if AB_TEST_ENABLED:
                    flow_id = assign_user_to_flow(wa_id)
                    default_state["flow"] = flow_id
                    default_state["flow_start_time"] = datetime.utcnow().isoformat()
                    logger.info(f"👤 New user {wa_id} assigned to Flow {flow_id}")
                else:
                    default_state["flow"] = "B"  # Use optimized flow only
                
                return default_state
            
            return json.loads(raw)
        except (redis.ConnectionError, json.JSONDecodeError) as e:
            logger.error(f"Error getting state for {wa_id}: {str(e)}")
            return default_state
    else:
        # Memory fallback
        return MEMORY_STORE.get(wa_id, default_state)
```

---

### **Step 3: Create Dynamic Question Handler**

Add this new function to handle both flows:

```python
def get_current_question(wa_id, state):
    """
    Get the current question based on user's flow and q_status
    """
    flow_id = state.get("flow", "A")
    q_status = state.get("q_status", 0)
    
    flow = AB_TEST_CONFIG["flows"][flow_id]
    
    # Map q_status to question ID
    question_map = {
        1: "q1",
        2: "q2",
        3: "q3",
        4: "q4",
        5: "q5",
        6: "q6" if flow_id == "A" else None  # Flow A has 6 questions
    }
    
    q_id = question_map.get(q_status)
    
    if q_id and q_id in flow["questions"]:
        return flow["questions"][q_id]
    
    return None


def send_question(wa_id, question_data):
    """
    Send question to user based on question type (buttons/list/text)
    """
    text = question_data["text"]
    question_type = question_data["type"]
    options = question_data.get("options", [])
    
    if question_type == "buttons":
        # Use WhatsApp buttons (max 3)
        send_whatsapp_message(wa_id, text, buttons=options)
    
    elif question_type == "list":
        # Use WhatsApp list (4+ options)
        send_whatsapp_message(wa_id, text, list_options=options)
    
    elif question_type == "text":
        # Free text response
        send_whatsapp_message(wa_id, text)
```

---

### **Step 4: Update Webhook Handler**

Replace the hardcoded question logic with dynamic flow:

```python
@app.route("/webhook", methods=["GET", "POST"])
@limiter.limit("20 per minute")
def webhook():
    """
    Webhook endpoint for Meta WhatsApp Business API
    NOW with A/B testing support
    """
    if request.method == "GET":
        # Webhook verification (keep as is)
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if verify_token == WEBHOOK_VERIFY_TOKEN:
            logger.info("✅ Webhook verification successful")
            return challenge
        else:
            logger.warning(f"⚠️  Webhook verification failed")
            return "Forbidden", 403
    
    # POST request - incoming message
    data = request.get_json()
    
    # Extract message details
    messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    
    if not messages:
        return jsonify({"status": "ok", "step": "no_messages"})
    
    message = messages[0]
    wa_id = message.get("from")
    message_type = message.get("type")
    
    # Get user state (includes flow assignment)
    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})
    flow_id = state.get("flow", "A")
    
    # Track start if new user
    if q_status == 0:
        track_ab_test_metric(flow_id, "total_starts", 1)
        state["q_status"] = 1
        save_user_state(wa_id, state)
        
        # Send first question
        question = get_current_question(wa_id, state)
        send_question(wa_id, question)
        
        return jsonify({"status": "ok", "step": "started", "flow": flow_id})
    
    # Extract incoming message
    incoming_text = None
    
    if message_type == "text":
        incoming_text = message.get("text", {}).get("body", "").strip()
    elif message_type == "button":
        incoming_text = message.get("button", {}).get("text", "").strip()
    elif message_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            incoming_text = interactive.get("button_reply", {}).get("title", "").strip()
        elif interactive.get("type") == "list_reply":
            incoming_text = interactive.get("list_reply", {}).get("title", "").strip()
    
    if not incoming_text:
        return jsonify({"status": "ok", "step": "no_text"})
    
    # Store answer
    current_q = f"q{q_status}"
    answers[current_q] = incoming_text
    state["answers"] = answers
    
    # Move to next question
    state["q_status"] = q_status + 1
    save_user_state(wa_id, state)
    
    # Check if conversation is complete
    flow = AB_TEST_CONFIG["flows"][flow_id]
    max_questions = len(flow["questions"])
    
    if state["q_status"] > max_questions:
        # Conversation complete!
        complete_lead_with_ab_test(wa_id, state)
        return jsonify({"status": "ok", "step": "completed", "flow": flow_id})
    
    # Send next question
    question = get_current_question(wa_id, state)
    if question:
        send_question(wa_id, question)
    
    return jsonify({"status": "ok", "step": f"q{state['q_status']}", "flow": flow_id})
```

---

### **Step 5: Update Lead Completion Handler**

```python
def complete_lead_with_ab_test(wa_id, state):
    """
    Complete lead and track A/B test metrics
    """
    answers = state["answers"]
    flow_id = state["flow"]
    
    # Calculate lead score based on flow
    score = calculate_lead_score(flow_id, answers)
    lead_category = determine_lead_category(score, flow_id)
    
    # Track completion
    track_ab_test_metric(flow_id, "total_completions", 1)
    
    # Track lead quality
    if lead_category == "HOT":
        track_ab_test_metric(flow_id, "hot_leads", 1)
    elif lead_category == "WARM":
        track_ab_test_metric(flow_id, "warm_leads", 1)
    else:
        track_ab_test_metric(flow_id, "cold_leads", 1)
    
    # Calculate time to complete
    start_time = datetime.fromisoformat(state["flow_start_time"])
    end_time = datetime.utcnow()
    duration = (end_time - start_time).total_seconds()
    track_ab_test_metric(flow_id, "total_time", duration)
    
    # Map answers to standard format
    lead_data = map_answers_to_lead_data(answers, flow_id)
    lead_data["lead_score"] = lead_category
    lead_data["ab_test_flow"] = flow_id
    lead_data["completion_time_seconds"] = duration
    
    # Log to Google Sheets
    log_lead_to_sheet(lead_data)
    
    # Send thank you message
    send_completion_message(wa_id, lead_category, lead_data)
    
    # Reset state
    reset_user_state(wa_id)
    
    logger.info(f"✅ Lead completed - Flow {flow_id} - {lead_category} - {duration}s")


def map_answers_to_lead_data(answers, flow_id):
    """
    Map answers from different flows to standard lead data format
    """
    flow = AB_TEST_CONFIG["flows"][flow_id]
    
    # Initialize lead data
    lead_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "wa_id": None,  # Will be set by caller
        "name": None,
        "phone": None,
        "car_type": None,      # New or Used
        "car_model": None,
        "budget": None,
        "timeline": None,
        "test_drive": None,
        "preferred_time": None
    }
    
    if flow_id == "A":
        # Flow A mapping: Q1=Type, Q2=Model, Q3=Budget, Q4=Timeline, Q5=TestDrive, Q6=Contact
        lead_data["car_type"] = answers.get("q1")
        lead_data["car_model"] = answers.get("q2")
        lead_data["budget"] = answers.get("q3")
        lead_data["timeline"] = answers.get("q4")
        lead_data["test_drive"] = answers.get("q5")
        
        # Parse contact details from Q6
        contact = answers.get("q6", "")
        name, phone, time = parse_contact_details(contact)
        lead_data["name"] = name
        lead_data["phone"] = phone
        lead_data["preferred_time"] = time
    
    elif flow_id == "B":
        # Flow B mapping: Q1=Timeline, Q2=Type, Q3=Budget, Q4=Model, Q5=Contact
        lead_data["timeline"] = answers.get("q1")
        lead_data["car_type"] = answers.get("q2")
        lead_data["budget"] = answers.get("q3")
        lead_data["car_model"] = answers.get("q4")
        
        # Parse contact details from Q5
        contact = answers.get("q5", "")
        name, phone, time = parse_contact_details(contact)
        lead_data["name"] = name
        lead_data["phone"] = phone
        lead_data["preferred_time"] = time
        
        # Flow B doesn't have separate test drive question
        # Infer from timeline and contact
        if "This week" in lead_data["timeline"] or "This month" in lead_data["timeline"]:
            lead_data["test_drive"] = "Yes, book test drive"
        else:
            lead_data["test_drive"] = "Maybe later"
    
    return lead_data


def parse_contact_details(contact_text):
    """
    Parse "Name, Phone, Time" from contact text
    """
    import re
    
    # Extract phone number (10 digits)
    phone_match = re.search(r'\b\d{10}\b', contact_text)
    phone = phone_match.group(0) if phone_match else "Not provided"
    
    # Extract name (first 2-3 words before phone)
    name_match = re.search(r'^([A-Za-z\s]{3,30})', contact_text)
    name = name_match.group(1).strip() if name_match else "Not provided"
    
    # Extract time (everything after phone)
    time_part = contact_text.split(phone)[-1].strip(', ') if phone != "Not provided" else ""
    preferred_time = time_part if time_part else "Not specified"
    
    return name, phone, preferred_time
```

---

### **Step 6: Create A/B Test Dashboard Endpoint**

Add this to see results:

```python
@app.route("/ab-test-results", methods=["GET"])
@limiter.exempt
def ab_test_results():
    """
    View A/B test results dashboard
    """
    # Get metrics for both flows
    flow_a_metrics = get_flow_metrics_from_redis("A")
    flow_b_metrics = get_flow_metrics_from_redis("B")
    
    # Calculate completion rates
    flow_a_completion = flow_a_metrics["completions"] / flow_a_metrics["starts"] if flow_a_metrics["starts"] > 0 else 0
    flow_b_completion = flow_b_metrics["completions"] / flow_b_metrics["starts"] if flow_b_metrics["starts"] > 0 else 0
    
    # Calculate HOT lead percentages
    flow_a_hot_pct = flow_a_metrics["hot"] / flow_a_metrics["completions"] if flow_a_metrics["completions"] > 0 else 0
    flow_b_hot_pct = flow_b_metrics["hot"] / flow_b_metrics["completions"] if flow_b_metrics["completions"] > 0 else 0
    
    # Determine winner
    winner = None
    if flow_a_metrics["completions"] >= 100 and flow_b_metrics["completions"] >= 100:
        if flow_b_hot_pct > flow_a_hot_pct * 1.10:  # 10% improvement
            winner = "Flow B (Optimized)"
        elif flow_a_hot_pct > flow_b_hot_pct * 1.10:
            winner = "Flow A (Original)"
        else:
            winner = "No clear winner yet"
    
    results = {
        "test_status": "RUNNING" if winner is None else "COMPLETE",
        "winner": winner,
        
        "flow_a": {
            "name": "Original Flow (New/Used first)",
            "starts": flow_a_metrics["starts"],
            "completions": flow_a_metrics["completions"],
            "completion_rate": f"{flow_a_completion:.1%}",
            "hot_leads": flow_a_metrics["hot"],
            "warm_leads": flow_a_metrics["warm"],
            "cold_leads": flow_a_metrics["cold"],
            "hot_percentage": f"{flow_a_hot_pct:.1%}",
            "avg_time_seconds": flow_a_metrics["avg_time"]
        },
        
        "flow_b": {
            "name": "Optimized Flow (Timeline first)",
            "starts": flow_b_metrics["starts"],
            "completions": flow_b_metrics["completions"],
            "completion_rate": f"{flow_b_completion:.1%}",
            "hot_leads": flow_b_metrics["hot"],
            "warm_leads": flow_b_metrics["warm"],
            "cold_leads": flow_b_metrics["cold"],
            "hot_percentage": f"{flow_b_hot_pct:.1%}",
            "avg_time_seconds": flow_b_metrics["avg_time"]
        },
        
        "improvement": {
            "completion_rate": f"{(flow_b_completion - flow_a_completion) / flow_a_completion:.1%}" if flow_a_completion > 0 else "N/A",
            "hot_percentage": f"{(flow_b_hot_pct - flow_a_hot_pct) / flow_a_hot_pct:.1%}" if flow_a_hot_pct > 0 else "N/A"
        }
    }
    
    return jsonify(results)


def get_flow_metrics_from_redis(flow_id):
    """
    Get aggregated metrics for a flow from Redis
    """
    if not REDIS_AVAILABLE:
        return {
            "starts": 0, "completions": 0,
            "hot": 0, "warm": 0, "cold": 0,
            "avg_time": 0
        }
    
    try:
        starts = int(r.get(f"ab_test:{flow_id}:total_starts") or 0)
        completions = int(r.get(f"ab_test:{flow_id}:total_completions") or 0)
        hot = int(r.get(f"ab_test:{flow_id}:hot_leads") or 0)
        warm = int(r.get(f"ab_test:{flow_id}:warm_leads") or 0)
        cold = int(r.get(f"ab_test:{flow_id}:cold_leads") or 0)
        total_time = float(r.get(f"ab_test:{flow_id}:total_time") or 0)
        
        avg_time = total_time / completions if completions > 0 else 0
        
        return {
            "starts": starts,
            "completions": completions,
            "hot": hot,
            "warm": warm,
            "cold": cold,
            "avg_time": avg_time
        }
    except Exception as e:
        logger.error(f"Error getting flow metrics: {str(e)}")
        return {
            "starts": 0, "completions": 0,
            "hot": 0, "warm": 0, "cold": 0,
            "avg_time": 0
        }
```

---

## 📊 How to Run the A/B Test

### **Step 1: Enable A/B Test**
In `app.py`, set:
```python
AB_TEST_ENABLED = True
```

### **Step 2: Deploy & Monitor**
```bash
# Deploy your app
python app.py

# Or with Gunicorn
gunicorn -c gunicorn_config.py app:app
```

### **Step 3: Check Results**
Visit: `http://your-domain.com/ab-test-results`

### **Step 4: Analyze (After 200 Conversations)**
```json
{
  "test_status": "COMPLETE",
  "winner": "Flow B (Optimized)",
  
  "flow_a": {
    "completion_rate": "70.0%",
    "hot_percentage": "28.5%",
    "avg_time_seconds": 145
  },
  
  "flow_b": {
    "completion_rate": "79.0%",
    "hot_percentage": "42.3%",
    "avg_time_seconds": 128
  },
  
  "improvement": {
    "completion_rate": "+12.9%",
    "hot_percentage": "+48.4%"
  }
}
```

### **Step 5: Pick Winner & Turn Off Test**
If Flow B wins:
```python
AB_TEST_ENABLED = False  # Stop test, use Flow B for everyone
```

---

## ✅ Expected Results

### **My Prediction:**

**Flow B (Optimized) will win because:**
1. ✅ HOT leads identified in Q1 (vs Q4 in Flow A)
2. ✅ Budget check happens earlier (eliminates mismatches faster)
3. ✅ Psychological momentum (urgency → easy → medium → hard)
4. ✅ Natural conversation flow (when → what → how much → which)

**Expected improvements:**
- Completion rate: +10-15%
- HOT lead percentage: +30-50%
- Time to complete: -10-15%

**If I'm right:** Flow B becomes permanent  
**If I'm wrong:** Learn why and iterate

---

## 🎯 Next Steps

1. **Review the code changes** I've outlined
2. **Test locally** with a few test messages
3. **Deploy** and start collecting data
4. **Monitor** the `/ab-test-results` endpoint
5. **Make decision** after 200 conversations (100 per flow)

Want me to:
1. **Write the actual code changes** to integrate into your app.py?
2. **Create a simple dashboard** to visualize results?
3. **Set up automated winner detection** (email you when test completes)?

Let me know! 🚀
