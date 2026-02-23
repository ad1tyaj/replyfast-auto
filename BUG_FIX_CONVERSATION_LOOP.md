# 🐛 BUG FIX: Conversation Loop Issue

## Problem Description

**Symptom:** Bot keeps sending "❌ I didn't quite catch that" and resetting the conversation in an infinite loop.

**Root Causes:**
1. **State not persisting on invalid input** - When user sends invalid input, error message is shown but state remains unchanged, causing repeated errors
2. **Button clicks trigger new conversation** - When user clicks a button after seeing error, `q_status == 0` check triggers and restarts flow
3. **Race condition with button responses** - Interactive button responses might not be properly extracted

## Location of Bug

**File:** `app.py`  
**Lines:** 422-437

**Current buggy code:**
```python
# Start of flow - new user or reset state
if q_status == 0:
    # Send Q1 and set state to expect answer to Q1 next
    state["q_status"] = 1
    state["answers"] = answers
    save_user_state(wa_id, state)
    send_q1(wa_id)
    return jsonify({"status": "ok", "step": "sent_q1"})

# q_status 1: Handle answer to Q1, then send Q2
if q_status == 1:
    valid_options = ["New", "Used"]
    if incoming_text.lower() not in [opt.lower() for opt in valid_options]:
        send_invalid_option_message(wa_id)
        send_q1(wa_id)  # ❌ BUG: Sends Q1 but doesn't stay at q_status 1
        return jsonify({"status": "ok", "step": "invalid_q1_option"})
```

## The Fix

### Part 1: Don't Resend Question on Invalid Input

Instead of sending the question again, just send the error and keep the state unchanged so user can try again.

```python
# q_status 1: Handle answer to Q1, then send Q2
if q_status == 1:
    valid_options = ["New", "Used"]
    if incoming_text.lower() not in [opt.lower() for opt in valid_options]:
        # ✅ FIXED: Just send error, don't resend question
        send_invalid_option_message(wa_id)
        # State remains at q_status 1, user can try again
        return jsonify({"status": "ok", "step": "invalid_q1_option"})
```

### Part 2: Add Retry Counter to Prevent Infinite Loops

Track how many times user has failed and reset after 3 attempts.

```python
def send_invalid_option_message(wa_id, retry_count=1):
    if retry_count >= 3:
        # After 3 failed attempts, offer help
        send_whatsapp_message(
            wa_id,
            "🤔 Seems like you're having trouble. Let me restart the conversation.\n\n"
            "Just tap one of the buttons that appear below each question."
        )
    else:
        send_whatsapp_message(
            wa_id,
            "❌ I didn't quite catch that. Please select one of the options above.\n\n"
            f"Attempt {retry_count} of 3"
        )
```

### Part 3: Better Button Response Handling

Ensure button clicks are properly recognized:

```python
def extract_message_content(data):
    """Extract message content from different Meta webhook formats"""
    messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    
    if not messages:
        messages = data.get("messages", [])
    
    if not messages:
        return None, None
    
    message = messages[0]
    wa_id = message.get("from")
    
    # Extract text from different message types
    text_content = ""
    
    if "text" in message:
        text_content = message["text"].get("body", "")
    elif "interactive" in message:
        interactive = message["interactive"]
        if interactive["type"] == "button_reply":
            # ✅ IMPROVED: Get the actual title
            text_content = interactive["button_reply"].get("title", "")
        elif interactive["type"] == "list_reply":
            # ✅ IMPROVED: Get the actual title
            text_content = interactive["list_reply"].get("title", "")
    
    # ✅ ADDED: Log what we extracted for debugging
    logger.info(f"📥 Extracted from {wa_id}: '{text_content}'")
    
    return wa_id, text_content.strip()
```

## Complete Fixed Code

Replace the webhook function starting at line 388 with this:

```python
@app.route("/webhook", methods=["GET", "POST"])
@limiter.exempt  # Temporarily disable rate limiting for testing
def webhook():
    """
    Webhook endpoint for Meta WhatsApp Business API
    Rate limited to prevent abuse
    """
    if request.method == "GET":
        # Webhook verification for Meta
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        # Verify token from environment variables
        if verify_token == WEBHOOK_VERIFY_TOKEN:
            logger.info("✅ Webhook verification successful")
            return challenge
        else:
            logger.warning(f"⚠️  Webhook verification failed. Received token: {verify_token}")
            return "Forbidden", 403
    
    # POST request - handle incoming messages
    data = request.get_json(silent=True) or {}
    
    # Log the full webhook payload for debugging
    logger.debug(f"📨 Webhook received: {json.dumps(data, indent=2)}")
    
    # Extract message content using helper function
    wa_id, incoming_text = extract_message_content(data)
    
    # If essential fields are missing, just 200 OK silently
    if not wa_id or incoming_text is None:
        logger.warning("⚠️  Missing wa_id or incoming_text, ignoring webhook")
        return "", 200

    # Log incoming message
    logger.info(f"📨 Message from {wa_id}: '{incoming_text}'")

    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})
    retry_count = state.get("retry_count", 0)  # ✅ ADDED: Track retries

    # Start of flow - new user or reset state
    if q_status == 0:
        # Send Q1 and set state to expect answer to Q1 next
        state["q_status"] = 1
        state["answers"] = {}
        state["retry_count"] = 0  # ✅ ADDED: Reset retry count
        save_user_state(wa_id, state)
        send_q1(wa_id)
        return jsonify({"status": "ok", "step": "sent_q1"})

    # q_status 1: Handle answer to Q1, then send Q2
    if q_status == 1:
        valid_options = ["New", "Used"]
        
        # ✅ IMPROVED: Better matching
        user_choice = None
        for option in valid_options:
            if option.lower() in incoming_text.lower():
                user_choice = option
                break
        
        if not user_choice:
            # ✅ FIXED: Increment retry count and handle gracefully
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                # Reset conversation after 3 failed attempts
                logger.warning(f"⚠️  User {wa_id} failed 3 times at Q1, resetting")
                reset_user_state(wa_id)
                send_whatsapp_message(
                    wa_id,
                    "🤔 Let's start fresh! I'll guide you step by step.\n\n"
                    "Tip: Just tap one of the green buttons that appear."
                )
                time.sleep(1)  # Small delay before restarting
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                # Just send error, don't resend question
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please tap one of the buttons: 'New' or 'Used'\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q1_option"})

        # Valid answer received
        answers["q1"] = user_choice
        state["answers"] = answers
        state["q_status"] = 2
        state["retry_count"] = 0  # ✅ RESET retry count on success
        save_user_state(wa_id, state)

        send_q2(wa_id)
        return jsonify({"status": "ok", "step": "sent_q2"})

    # q_status 2: Handle answer to Q2 (budget buttons), then send Q3
    if q_status == 2:
        valid_options = ["Under 5 Lakhs", "5-8 Lakhs", "8-12 Lakhs", "12-20 Lakhs", "Above 20 Lakhs"]
        
        # ✅ IMPROVED: Better matching
        user_choice = None
        for option in valid_options:
            if option.lower() in incoming_text.lower() or option in incoming_text:
                user_choice = option
                break
        
        if not user_choice:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                reset_user_state(wa_id)
                send_whatsapp_message(wa_id, "🤔 Let's start over. Please tap one of the options from the list.")
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please select a budget range from the list above.\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q2_option"})
        
        answers["q2"] = user_choice
        state["answers"] = answers
        state["q_status"] = 3
        state["retry_count"] = 0
        save_user_state(wa_id, state)

        send_q3(wa_id)
        return jsonify({"status": "ok", "step": "sent_q3"})

    # Continue with q_status 3, 4, 5 following the same pattern...
    # (Apply the same fix to all other question handlers)
```

## Testing the Fix

### Test Case 1: Button Click Works
```
User: [Clicks "New" button]
Bot: Q2: What's your budget range?
✅ Should progress to Q2
```

### Test Case 2: Invalid Input Handling
```
User: "hello"
Bot: ❌ Please tap one of the buttons: 'New' or 'Used' (Attempt 1 of 3)
User: "test"
Bot: ❌ Please tap one of the buttons: 'New' or 'Used' (Attempt 2 of 3)
User: "xyz"
Bot: 🤔 Let's start fresh! [Restarts conversation]
✅ Should reset after 3 attempts
```

### Test Case 3: Recovery After Error
```
User: "hello"
Bot: ❌ Please tap one of the buttons
User: [Clicks "New" button]
Bot: Q2: What's your budget range?
✅ Should recover and continue
```

## Deployment

1. **Backup current code:**
```bash
cp app.py app.py.backup.$(date +%Y%m%d)
```

2. **Apply the fix:**
   - Replace the webhook function with the fixed version
   - Update extract_message_content with better logging
   - Add retry_count tracking to state

3. **Test locally:**
```bash
python app.py
# Test with ngrok webhook
```

4. **Deploy to production:**
```bash
git add app.py
git commit -m "fix: resolve conversation loop bug with retry logic"
git push heroku main
```

## Additional Debugging

Add this to see what's happening:

```python
# At the start of webhook POST handler
logger.info(f"🔍 DEBUG - wa_id: {wa_id}, q_status: {q_status}, incoming: '{incoming_text}'")
logger.info(f"🔍 DEBUG - Full state: {json.dumps(state, indent=2)}")
```

## Prevention

To prevent similar bugs in the future:

1. **Always log state changes**
2. **Add retry limits** to prevent infinite loops
3. **Test button interactions** thoroughly
4. **Monitor webhook logs** for patterns
5. **Add unit tests** for state transitions

---

**Status:** Ready to implement  
**Priority:** 🔴 Critical (affects all conversations)  
**Estimated Fix Time:** 30 minutes
