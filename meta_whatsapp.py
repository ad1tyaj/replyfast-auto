import requests
import json
from config import META_API_TOKEN, META_PHONE_ID


class MetaWhatsAppAPI:
    def __init__(self):
        self.default_token = META_API_TOKEN
        self.default_phone_id = META_PHONE_ID

        if not self.default_token or not self.default_phone_id:
            raise ValueError("META_API_TOKEN and META_PHONE_ID must be set in environment variables")

    # ─────────────────────────────────────────────────────────────
    # Internal helper — resolves per-call vs global credentials
    # ─────────────────────────────────────────────────────────────
    def _resolve_creds(self, access_token=None, phone_number_id=None):
        """Return (token, phone_id) using per-client values when supplied,
        falling back to the global .env defaults."""
        token    = access_token    or self.default_token
        phone_id = phone_number_id or self.default_phone_id
        return token, phone_id

    # ─────────────────────────────────────────────────────────────
    # Public send methods — all accept optional per-client creds
    # ─────────────────────────────────────────────────────────────

    def send_text_message(self, wa_id, message_text,
                          access_token=None, phone_number_id=None):
        """Send a simple text message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "text",
            "text": {"body": message_text}
        }
        return self._send_message(payload, access_token, phone_number_id)

    def send_interactive_button_message(self, wa_id, message_text, buttons,
                                        access_token=None, phone_number_id=None):
        """Send a message with interactive buttons (max 3 buttons)."""
        if len(buttons) > 3:
            raise ValueError("Meta WhatsApp API supports maximum 3 buttons")

        button_components = [
            {
                "type": "reply",
                "reply": {
                    "id": f"btn_{i+1}",
                    "title": btn[:20]   # Max 20 chars
                }
            }
            for i, btn in enumerate(buttons)
        ]

        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": message_text},
                "action": {"buttons": button_components}
            }
        }
        return self._send_message(payload, access_token, phone_number_id)

    def send_interactive_list_message(self, wa_id, message_text, header_text,
                                      list_items,
                                      access_token=None, phone_number_id=None):
        """Send a message with interactive list (for more than 3 options)."""
        if len(list_items) > 10:
            raise ValueError("Meta WhatsApp API supports maximum 10 list items")

        rows = [
            {
                "id": f"item_{i+1}",
                "title": item[:24],   # Max 24 chars
                "description": ""
            }
            for i, item in enumerate(list_items)
        ]

        payload = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header_text},
                "body": {"text": message_text},
                "action": {
                    "button": "Select Option",
                    "sections": [{"title": "Options", "rows": rows}]
                }
            }
        }
        return self._send_message(payload, access_token, phone_number_id)

    # ─────────────────────────────────────────────────────────────
    # Internal transport — builds URL & auth dynamically per call
    # ─────────────────────────────────────────────────────────────

    def _send_message(self, payload, access_token=None, phone_number_id=None):
        """Send message to Meta API using resolved credentials."""
        token, phone_id = self._resolve_creds(access_token, phone_number_id)

        url     = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json"
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            return {
                "success":     True,
                "response":    response.json(),
                "status_code": response.status_code,
                "phone_id":    phone_id   # handy for debugging
            }
        except requests.exceptions.RequestException as e:
            return {
                "success":     False,
                "error":       str(e),
                "status_code": getattr(e.response, 'status_code', None)
                               if hasattr(e, 'response') else None
            }


# Singleton — existing code that calls meta_api.send_* keeps working unchanged
meta_api = MetaWhatsAppAPI()