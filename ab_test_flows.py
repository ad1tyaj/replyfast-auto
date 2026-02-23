"""
A/B Testing Setup for ReplyFast Auto
Compares FLOW A (Current) vs FLOW B (Optimized)
"""

# ============================================================================
# FLOW A: CURRENT FLOW (Your Original)
# ============================================================================

FLOW_A = {
    "name": "Original Flow",
    "description": "New/Used → Model → Budget → Timeline → Contact",
    
    "questions": {
        "q1": {
            "text": "🚗 Welcome! Are you looking for a NEW or USED car?",
            "type": "buttons",
            "options": ["New Car", "Used Car"],
            "next": "q2"
        },
        
        "q2": {
            "text": "Great choice! Which model interests you most?",
            "type": "list",
            "options": [
                "Swift", "Baleno", "Dzire", "Brezza", "Ertiga", 
                "Wagon R", "Alto", "Other", "Not sure - need help"
            ],
            "next": "q3"
        },
        
        "q3": {
            "text": "Perfect! What's your budget range?",
            "type": "list",
            "options": [
                "Under 5 Lakhs",
                "5-8 Lakhs",
                "8-12 Lakhs",
                "12-20 Lakhs",
                "Above 20 Lakhs",
                "Flexible/Need advice"
            ],
            "next": "q4"
        },
        
        "q4": {
            "text": "When are you planning to buy?",
            "type": "buttons",
            "options": [
                "This week",
                "This month",
                "Next 3 months",
                "Just exploring"
            ],
            "next": "q5"
        },
        
        "q5": {
            "text": "Would you like to schedule a test drive?",
            "type": "buttons",
            "options": ["Yes, book test drive", "No, just want info"],
            "next": "q6"
        },
        
        "q6": {
            "text": """Perfect! Please share your contact details:
            
• Your name
• Phone number
• Preferred visit time

Example: Amit Kumar, 9876543210, Tomorrow 3 PM""",
            "type": "text",
            "next": "complete"
        }
    },
    
    "scoring": {
        # Score calculated only at the end
        "method": "final_only",
        "rules": {
            "timeline_this_week": 20,
            "timeline_this_month": 15,
            "timeline_3_months": 10,
            "timeline_exploring": 0,
            "test_drive_yes": 15,
            "test_drive_no": 5,
            "budget_matches": 10,
            "budget_mismatch": -5,
            "specific_model": 10,
            "not_sure_model": 5
        }
    }
}

# ============================================================================
# FLOW B: OPTIMIZED FLOW (New Recommended)
# ============================================================================

FLOW_B = {
    "name": "Optimized Flow",
    "description": "Timeline → New/Used → Budget → Model → Contact",
    
    "questions": {
        "q1": {
            "text": "🚗 Welcome to [Dealership]!\n\nWhen are you planning to buy a car?",
            "type": "buttons",
            "options": [
                "This week 🔥",
                "This month",
                "Next 3 months",
                "Just exploring"
            ],
            "next": "q2",
            "scoring": {
                "This week 🔥": 20,
                "This month": 15,
                "Next 3 months": 10,
                "Just exploring": 0
            }
        },
        
        "q2": {
            "text": "Perfect! Are you looking for:",
            "type": "buttons",
            "options": ["New Car", "Used Car"],
            "next": "q3",
            "scoring": {
                "New Car": 5,
                "Used Car": 3
            }
        },
        
        "q3": {
            "text": "Great choice! What's your budget range?",
            "type": "list",
            "options": [
                "Under 5 Lakhs",
                "5-8 Lakhs",
                "8-12 Lakhs",
                "12-20 Lakhs",
                "Above 20 Lakhs",
                "Flexible/Need advice"
            ],
            "next": "q4",
            "scoring": {
                "method": "dynamic",  # Check if budget matches car prices
                "flexible": 5
            }
        },
        
        "q4": {
            "text": "Excellent! Which model are you interested in?",
            "type": "list",
            "options": [
                "Swift", "Baleno", "Dzire", "Brezza", "Ertiga",
                "Wagon R", "Alto", "Other", "Not sure - need help"
            ],
            "next": "q5",
            "scoring": {
                "specific_model": 10,
                "not_sure": 3
            }
        },
        
        "q5": {
            "text": """Perfect! Let's get you behind the wheel! 🚗

To schedule your test drive, please share:
• Your name
• Phone number
• Preferred visit time

Example: Amit Kumar, 9876543210, Tomorrow 3 PM""",
            "type": "text",
            "next": "complete",
            "scoring": {
                "has_specific_time": 10,
                "vague_time": 5
            }
        }
    },
    
    "scoring": {
        # Progressive scoring (score after each question)
        "method": "progressive",
        "thresholds": {
            "hot": 60,    # 60+ = HOT lead
            "warm": 30,   # 30-59 = WARM lead
            "cold": 0     # 0-29 = COLD lead
        }
    }
}

# ============================================================================
# A/B TEST CONFIGURATION
# ============================================================================

AB_TEST_CONFIG = {
    "enabled": True,
    "split": "50/50",  # 50% see Flow A, 50% see Flow B
    
    "assignment_method": "random",  # or "alternate", "hash"
    
    "flows": {
        "A": FLOW_A,
        "B": FLOW_B
    },
    
    "metrics_to_track": [
        "completion_rate",      # % who complete all questions
        "time_to_complete",     # Average time in seconds
        "lead_quality_score",   # Average score (HOT/WARM/COLD)
        "conversion_rate",      # % who provide contact details
        "dropout_at_each_step", # Where do people quit?
        "hot_lead_percentage",  # % of HOT leads
    ],
    
    "minimum_sample_size": 100,  # Need 100 users per variant
    
    "winner_criteria": {
        "primary": "hot_lead_percentage",  # Main metric to optimize
        "secondary": "completion_rate",     # Tiebreaker
        "statistical_significance": 0.95    # 95% confidence
    }
}

# ============================================================================
# TRACKING DATA STRUCTURE
# ============================================================================

"""
Store in Redis or database:

ab_test_data = {
    "flow_a": {
        "total_starts": 150,
        "total_completions": 105,
        "completion_rate": 0.70,
        "avg_time_seconds": 142,
        "hot_leads": 32,
        "warm_leads": 48,
        "cold_leads": 25,
        "hot_percentage": 0.30,
        "dropout_by_question": {
            "q1": 5,
            "q2": 12,
            "q3": 15,
            "q4": 8,
            "q5": 5
        }
    },
    
    "flow_b": {
        "total_starts": 148,
        "total_completions": 118,
        "completion_rate": 0.80,
        "avg_time_seconds": 128,
        "hot_leads": 52,
        "warm_leads": 42,
        "cold_leads": 24,
        "hot_percentage": 0.44,
        "dropout_by_question": {
            "q1": 3,
            "q2": 8,
            "q3": 10,
            "q4": 6,
            "q5": 3
        }
    }
}
"""

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def assign_user_to_flow(user_id):
    """
    Randomly assign user to Flow A or Flow B
    """
    import random
    
    # Method 1: Pure random (50/50)
    if AB_TEST_CONFIG["assignment_method"] == "random":
        return "A" if random.random() < 0.5 else "B"
    
    # Method 2: Alternate (A, B, A, B, A, B...)
    elif AB_TEST_CONFIG["assignment_method"] == "alternate":
        # Use user count to alternate
        count = get_total_user_count()
        return "A" if count % 2 == 0 else "B"
    
    # Method 3: Hash-based (consistent per user)
    elif AB_TEST_CONFIG["assignment_method"] == "hash":
        import hashlib
        hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
        return "A" if hash_val % 2 == 0 else "B"


def get_flow_for_user(user_id):
    """
    Get the flow assigned to this user
    """
    # Check if user already assigned
    assigned_flow = get_user_assignment(user_id)
    
    if assigned_flow:
        return AB_TEST_CONFIG["flows"][assigned_flow]
    
    # New user - assign to flow
    flow_id = assign_user_to_flow(user_id)
    save_user_assignment(user_id, flow_id)
    
    return AB_TEST_CONFIG["flows"][flow_id]


def calculate_lead_score(flow_id, answers):
    """
    Calculate lead score based on flow type
    """
    flow = AB_TEST_CONFIG["flows"][flow_id]
    scoring_method = flow["scoring"]["method"]
    
    if scoring_method == "final_only":
        # Flow A: Calculate at end
        return calculate_final_score(answers, flow["scoring"]["rules"])
    
    elif scoring_method == "progressive":
        # Flow B: Progressive scoring
        return calculate_progressive_score(answers, flow)


def calculate_progressive_score(answers, flow):
    """
    Calculate score progressively (Flow B)
    """
    score = 0
    
    for q_id, answer in answers.items():
        question = flow["questions"][q_id]
        
        if "scoring" in question:
            if answer in question["scoring"]:
                score += question["scoring"][answer]
            elif "specific_model" in question["scoring"]:
                # Check if it's a specific model or "not sure"
                if answer != "Not sure - need help":
                    score += question["scoring"]["specific_model"]
                else:
                    score += question["scoring"]["not_sure"]
    
    return score


def determine_lead_category(score, flow_id):
    """
    Determine if lead is HOT/WARM/COLD
    """
    flow = AB_TEST_CONFIG["flows"][flow_id]
    
    if flow["scoring"]["method"] == "progressive":
        thresholds = flow["scoring"]["thresholds"]
        
        if score >= thresholds["hot"]:
            return "HOT"
        elif score >= thresholds["warm"]:
            return "WARM"
        else:
            return "COLD"
    
    else:
        # Flow A: Use original logic
        if score >= 50:
            return "HOT"
        elif score >= 25:
            return "WARM"
        else:
            return "COLD"


def track_ab_test_metric(flow_id, metric, value):
    """
    Track metrics for A/B test
    """
    # Store in Redis or database
    metrics_key = f"ab_test:{flow_id}:{metric}"
    
    # Update counter
    increment_metric(metrics_key, value)
    
    # Log for analysis
    log_ab_test_event({
        "flow": flow_id,
        "metric": metric,
        "value": value,
        "timestamp": datetime.utcnow().isoformat()
    })


def get_ab_test_results():
    """
    Get current A/B test results
    """
    results = {
        "flow_a": get_flow_metrics("A"),
        "flow_b": get_flow_metrics("B"),
        "winner": None,
        "confidence": None
    }
    
    # Determine winner using statistical significance
    if results["flow_a"]["total_starts"] >= AB_TEST_CONFIG["minimum_sample_size"]:
        winner, confidence = calculate_winner(results["flow_a"], results["flow_b"])
        results["winner"] = winner
        results["confidence"] = confidence
    
    return results


def calculate_winner(flow_a_metrics, flow_b_metrics):
    """
    Calculate winner using statistical significance test
    """
    from scipy import stats
    
    # Primary metric: Hot lead percentage
    a_hot_rate = flow_a_metrics["hot_percentage"]
    b_hot_rate = flow_b_metrics["hot_percentage"]
    
    # Calculate if difference is statistically significant
    # Using z-test for proportions
    n_a = flow_a_metrics["total_completions"]
    n_b = flow_b_metrics["total_completions"]
    
    # Calculate z-score
    p_pooled = (a_hot_rate * n_a + b_hot_rate * n_b) / (n_a + n_b)
    se = (p_pooled * (1 - p_pooled) * (1/n_a + 1/n_b)) ** 0.5
    z_score = (b_hot_rate - a_hot_rate) / se
    
    # Calculate p-value
    p_value = 2 * (1 - stats.norm.cdf(abs(z_score)))
    
    confidence = 1 - p_value
    
    if confidence >= AB_TEST_CONFIG["winner_criteria"]["statistical_significance"]:
        winner = "B" if b_hot_rate > a_hot_rate else "A"
    else:
        winner = None  # No clear winner yet
    
    return winner, confidence


# ============================================================================
# REDIS HELPER FUNCTIONS (Placeholders)
# ============================================================================

def get_user_assignment(user_id):
    """Get user's assigned flow from Redis"""
    # Implementation: r.get(f"ab_test_assignment:{user_id}")
    pass

def save_user_assignment(user_id, flow_id):
    """Save user's flow assignment to Redis"""
    # Implementation: r.set(f"ab_test_assignment:{user_id}", flow_id)
    pass

def get_total_user_count():
    """Get total user count for alternate assignment"""
    # Implementation: r.get("ab_test_user_count")
    pass

def increment_metric(key, value):
    """Increment metric counter in Redis"""
    # Implementation: r.incr(key, value)
    pass

def log_ab_test_event(event):
    """Log event for analysis"""
    # Implementation: r.lpush("ab_test_events", json.dumps(event))
    pass

def get_flow_metrics(flow_id):
    """Get metrics for a specific flow"""
    # Implementation: Fetch from Redis and calculate
    pass
