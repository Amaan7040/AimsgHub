# sms_service.py
import logging
from services.database import get_sms_users_collection, get_sms_logs_collection
from config import twilio_client
from datetime import datetime, timezone
from services.api_key_service import APIKeyService

logger = logging.getLogger(__name__)

async def send_sms(req, user_id: str):
    """Send SMS message with user authentication"""
    from models.marketing import SMSRequest
    
    if not isinstance(req, SMSRequest):
        raise ValueError("Request must be SMSRequest instance")
    
    if not twilio_client:
        raise Exception("Twilio client not configured")
        
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": user_id})
    
    if not user or not user.get("verified_number"):
        raise Exception("User number not verified")
        
    verified_number = user["verified_number"]
    try:
        message = twilio_client.messages.create(
            body=req.message,
            from_=verified_number,
            to=req.to_number
        )
        
        sms_logs_collection = await get_sms_logs_collection()
        await sms_logs_collection.insert_one({
            "user_id": user_id,
            "to_number": req.to_number,
            "message": req.message,
            "sid": message.sid,
            "status": "sent",
            "timestamp": datetime.now(timezone.utc)
        })
        
        return {"message": "SMS sent", "sid": message.sid}
    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        raise e