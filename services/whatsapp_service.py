import requests
import logging
from config import WHATSAPP_API_URL
import json

logger = logging.getLogger(__name__)

def send_whatsapp_message(phone_number_id, to_number, message, access_token):
    """Send WhatsApp text message via Meta API"""
    if not phone_number_id or not access_token:
        logger.error("Missing WhatsApp credentials")
        return None
        
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message}}
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return None

def send_whatsapp_media(phone_number_id, to_number, media_url, caption, access_token, media_type="image"):
    """Send WhatsApp media message"""
    if not phone_number_id or not access_token:
        logger.error("Missing WhatsApp credentials")
        return None
        
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    media_types = {
        "image": "image",
        "video": "video", 
        "audio": "audio",
        "document": "document"
    }
    
    actual_type = media_types.get(media_type, "image")
    
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": actual_type,
        actual_type: {
            "link": media_url,
            "caption": caption
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp media: {e}")
        return None

def send_whatsapp_interactive(phone_number_id, to_number, interactive_data, access_token):
    """Send interactive message (buttons, lists)"""
    if not phone_number_id or not access_token:
        logger.error("Missing WhatsApp credentials")
        return None
        
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": interactive_data
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp interactive message: {e}")
        return None

def send_whatsapp_template(phone_number_id, to_number, template_name, template_components, access_token):
    """Send WhatsApp template message"""
    if not phone_number_id or not access_token:
        logger.error("Missing WhatsApp credentials")
        return None
        
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": template_components
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp template: {e}")
        return None

def create_button_message(body_text, buttons):
    """Create button message structure"""
    button_objects = []
    for i, button in enumerate(buttons):
        button_objects.append({
            "type": "reply",
            "reply": {
                "id": f"btn_{i+1}",
                "title": button.get("title", f"Button {i+1}")
            }
        })
    
    return {
        "type": "button",
        "body": {"text": body_text},
        "action": {
            "buttons": button_objects
        }
    }

def create_list_message(header_text, body_text, sections):
    """Create list message structure"""
    return {
        "type": "list",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {
            "button": "View Options",
            "sections": sections
        }
    }