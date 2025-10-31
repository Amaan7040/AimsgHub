from fastapi import APIRouter, HTTPException, Request, Response
from models.marketing import NumberRequest, OTPVerifyRequest, SMSRequest
from services.database import get_sms_users_collection, get_sms_logs_collection
from config import twilio_client
from datetime import datetime, timezone
import logging
from bson import ObjectId
from services.database import get_users_collection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sms", tags=["SMS Marketing"])

@router.post("/register_number")
async def register_number(req: NumberRequest):
    if not twilio_client:
        raise HTTPException(status_code=500, detail="Twilio client not configured")
        
    try:
        # Fetch user from database to get username
        users_collection = await get_users_collection()
        user = await users_collection.find_one({"_id": ObjectId(req.user_id)})
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get username from database
        username = user.get("username", "User")
        user_id_short = str(req.user_id)[-4:]  # Last 4 chars of user_id
        
        # Combine username and short ID, ensure under 30 chars
        friendly_name = f"{username}_{user_id_short}"[:30]
        
        service = twilio_client.verify.services.create(
            friendly_name=friendly_name
        )
        service_sid = service.sid
        
        twilio_client.verify.services(service_sid).verifications.create(
            to=req.phone_number,
            channel="sms"
        )
        
        sms_users_collection = await get_sms_users_collection()
        await sms_users_collection.update_one(
            {"user_id": req.user_id},
            {"$set": {
                "verified_number": req.phone_number,
                "verify_sid": service_sid,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )
        
        return {"message": "OTP sent to your number", "service_sid": service_sid}
    except Exception as e:
        logger.error(f"Error registering number: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
@router.post("/verify_number")
async def verify_number(req: OTPVerifyRequest):
    if not twilio_client:
        raise HTTPException(status_code=500, detail="Twilio client not configured")
        
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": req.user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    verified_number = user["verified_number"]
    service_sid = user["verify_sid"]
    
    try:
        verification_check = twilio_client.verify.services(service_sid).verification_checks.create(
            to=verified_number,
            code=req.code
        )
        if verification_check.status == "approved":
            return {"message": "Number verified successfully"}
        else:
            raise HTTPException(status_code=400, detail="Invalid OTP")
    except Exception as e:
        logger.error(f"Error verifying number: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/send")
async def send_sms(req: SMSRequest):
    if not twilio_client:
        raise HTTPException(status_code=500, detail="Twilio client not configured")
        
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": req.user_id})
    
    if not user or not user.get("verified_number"):
        raise HTTPException(status_code=404, detail="User number not verified")
        
    verified_number = user["verified_number"]
    try:
        message = twilio_client.messages.create(
            body=req.message,
            from_=verified_number,
            to=req.to_number
        )
        
        sms_logs_collection = await get_sms_logs_collection()
        await sms_logs_collection.insert_one({
            "user_id": req.user_id,
            "to_number": req.to_number,
            "message": req.message,
            "sid": message.sid,
            "status": "sent",
            "timestamp": datetime.now(timezone.utc)
        })
        
        return {"message": "SMS sent", "sid": message.sid}
    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webhook")
async def twilio_webhook(request: Request):
    data = await request.form()
    message_sid = data.get("MessageSid")
    sms_status = data.get("SmsStatus")
    
    sms_logs_collection = await get_sms_logs_collection()
    await sms_logs_collection.update_one(
        {"sid": message_sid},
        {"$set": {"status": sms_status}}
    )
    
    logger.info(f"SMS status update: {message_sid} -> {sms_status}")
    return "OK"