from fastapi import APIRouter, HTTPException, Request, Response
from models.marketing import NumberRequest, OTPVerifyRequest, SMSRequest
from services.database import get_sms_users_collection, get_sms_logs_collection
from config import twilio_client, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from datetime import datetime, timezone
import logging
from bson import ObjectId
from services.database import get_users_collection
from twilio.rest import Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sms", tags=["SMS Marketing"])

async def create_twilio_subaccount(user_id: str, friendly_name: str):
    """Create Twilio sub-account for user"""
    if not twilio_client:
        raise HTTPException(status_code=500, detail="Twilio client not configured")
    
    try:
        subaccount = twilio_client.api.accounts.create(
            friendly_name=friendly_name
        )
        
        return {
            "subaccount_sid": subaccount.sid,
            "subaccount_auth_token": subaccount.auth_token  # Note: This is only available at creation
        }
    except Exception as e:
        logger.error(f"Error creating Twilio subaccount: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to create subaccount: {str(e)}")

def get_twilio_subaccount_client(subaccount_sid: str, subaccount_auth_token: str):
    """Get Twilio client for specific sub-account - REMOVED ASYNC"""
    return Client(subaccount_sid, subaccount_auth_token)

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
        
        # Combine username and short ID for friendly name
        friendly_name = f"{username}_{user_id_short}"[:30]
        
        # Step 1: Create Twilio sub-account for user
        subaccount_data = await create_twilio_subaccount(req.user_id, friendly_name)
        
        # Step 2: Create verification service using sub-account
        subaccount_client = get_twilio_subaccount_client(  # REMOVED AWAIT
            subaccount_data["subaccount_sid"],
            subaccount_data["subaccount_auth_token"]
        )
        
        service = subaccount_client.verify.services.create(
            friendly_name=friendly_name
        )
        service_sid = service.sid
        
        # Send OTP using sub-account
        subaccount_client.verify.services(service_sid).verifications.create(
            to=req.phone_number,
            channel="sms"
        )
        
        # Store user data with sub-account information
        sms_users_collection = await get_sms_users_collection()
        await sms_users_collection.update_one(
            {"user_id": req.user_id},
            {"$set": {
                "verified_number": req.phone_number,
                "verify_sid": service_sid,
                "subaccount_sid": subaccount_data["subaccount_sid"],
                "subaccount_auth_token": subaccount_data["subaccount_auth_token"],
                "friendly_name": friendly_name,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )
        
        return {
            "message": "OTP sent to your number", 
            "service_sid": service_sid
        }
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
    
    # Use sub-account client for verification
    subaccount_client = get_twilio_subaccount_client(  # REMOVED AWAIT
        user["subaccount_sid"],
        user["subaccount_auth_token"]
    )
    
    try:
        verification_check = subaccount_client.verify.services(service_sid).verification_checks.create(
            to=verified_number,
            code=req.code
        )
        if verification_check.status == "approved":
            # Mark number as verified
            await sms_users_collection.update_one(
                {"user_id": req.user_id},
                {"$set": {"number_verified": True}}
            )
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
    
    if not user or not user.get("verified_number") or not user.get("number_verified"):
        raise HTTPException(status_code=404, detail="User number not verified")
    
    # Use sub-account client for sending
    subaccount_client = get_twilio_subaccount_client(  # REMOVED AWAIT
        user["subaccount_sid"],
        user["subaccount_auth_token"]
    )
    
    verified_number = user["verified_number"]
    try:
        message = subaccount_client.messages.create(
            body=req.message,
            from_=verified_number,
            to=req.to_number
        )
        
        # Log the SMS
        sms_logs_collection = await get_sms_logs_collection()
        await sms_logs_collection.insert_one({
            "user_id": req.user_id,
            "to_number": req.to_number,
            "from_number": verified_number,
            "message": req.message,
            "sid": message.sid,
            "status": "sent",
            "timestamp": datetime.now(timezone.utc)
        })
        
        return {
            "message": "SMS sent", 
            "sid": message.sid
        }
    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        
        # Log failed attempt
        sms_logs_collection = await get_sms_logs_collection()
        await sms_logs_collection.insert_one({
            "user_id": req.user_id,
            "to_number": req.to_number,
            "from_number": verified_number,
            "message": req.message,
            "status": "failed",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc)
        })
        
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