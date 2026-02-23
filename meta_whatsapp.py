import requests
import json
from config import META_API_TOKEN, META_PHONE_ID

class MetaWhatsAppAPI:
    def __init__(self):
        self.api_token = META_API_TOKEN
        self.phone_id = META_PHONE_ID
        self.base_url = f"https://graph.facebook.com/v18.0/{self.phone_id}/messages"
        
        if not self.api_token or not self.phone_id:
            raise ValueError("META_API_TOKEN and META_PHONE_ID must be set in environment variables")
    
    def send_text_message(self, wa_id, message_text):
        """Send a simple text message"""
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "text",
            "text": {
                "body": message_text
            }
        }
        return self._send_message(payload)
    
    def send_interactive_button_message(self, wa_id, message_text, buttons):
        """Send a message with interactive buttons (max 3 buttons)"""
        if len(buttons) > 3:
            raise ValueError("Meta WhatsApp API supports maximum 3 buttons")
        
        # Create button components
        button_components = []
        for i, button_text in enumerate(buttons):
            button_components.append({
                "type": "reply",
                "reply": {
                    "id": f"btn_{i+1}",
                    "title": button_text[:20]  # Max 20 characters for button title
                }
            })
        
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": message_text
                },
                "action": {
                    "buttons": button_components
                }
            }
        }
        return self._send_message(payload)
    
    def send_interactive_list_message(self, wa_id, message_text, header_text, list_items):
        """Send a message with interactive list (for more than 3 options)"""
        if len(list_items) > 10:
            raise ValueError("Meta WhatsApp API supports maximum 10 list items")
        
        # Create list row components
        rows = []
        for i, item_text in enumerate(list_items):
            rows.append({
                "id": f"item_{i+1}",
                "title": item_text[:24],  # Max 24 characters for list item title
                "description": ""
            })
        
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": header_text
                },
                "body": {
                    "text": message_text
                },
                "action": {
                    "button": "Select Option",
                    "sections": [
                        {
                            "title": "Options",
                            "rows": rows
                        }
                    ]
                }
            }
        }
        return self._send_message(payload)
    
    def _send_message(self, payload):
        """Internal method to send message to Meta API"""
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            return {
                "success": True,
                "response": response.json(),
                "status_code": response.status_code
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": str(e),
                "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            }

# Initialize the Meta API client
meta_api = MetaWhatsAppAPI()