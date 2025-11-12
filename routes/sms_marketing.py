# sms_marketing.py
from fastapi import APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Header, status, Body
from models.marketing import BusinessVerifyRequest, NumberRequest, OTPVerifyRequest, SMSRequest
from services.database import get_sms_users_collection, get_sms_logs_collection, get_twilio_numbers_collection, get_business_profiles_collection, get_users_collection
from config import twilio_client, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from datetime import datetime, timezone, timedelta
import logging
from bson import ObjectId
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import re
import pandas as pd
import io
from services.api_key_service import APIKeyService
from services.database import get_api_keys_collection
from typing import List, Optional
from pydantic import BaseModel, validator
import json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sms", tags=["SMS Marketing"])

# Dependency to get current user from API key (same pattern as email marketing)
async def get_current_user_from_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Get current user from SMS marketing API key
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required"
        )
    
    # Validate the API key with sms_marketing scope
    validation_result = APIKeyService.validate_api_key(x_api_key, "sms_marketing")
    
    if not validation_result["valid"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid API key: {validation_result.get('error', 'Authentication failed')}"
        )
    
    return {
        "user_id": validation_result["user_id"],
        "scope": validation_result["scope"]
    }

async def get_current_user_id(current_user: dict = Depends(get_current_user_from_api_key)):
    """
    Extract user_id from API key validation result
    """
    return current_user["user_id"]

class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super(JSONEncoder, self).default(obj)

def safe_convert_document(doc):
    """Safely convert MongoDB document to JSON-serializable format"""
    if not doc:
        return doc
    
    # Create a copy to avoid modifying the original
    result = doc.copy()
    
    # Convert ObjectId to string
    if "_id" in result:
        result["_id"] = str(result["_id"])
    
    # Convert user_id if it exists and is ObjectId
    if "user_id" in result and isinstance(result["user_id"], ObjectId):
        result["user_id"] = str(result["user_id"])
    
    # Convert datetime fields to ISO format strings
    datetime_fields = ["created_at", "updated_at", "timestamp", "sent_at", "number_purchased_at", "verified_at"]
    for field in datetime_fields:
        if field in result and result[field]:
            if isinstance(result[field], datetime):
                result[field] = result[field].isoformat()
            elif result[field]:
                result[field] = str(result[field])
    
    # Ensure list fields are properly formatted
    list_fields = ["contacts", "recipients"]
    for field in list_fields:
        if field in result:
            if not isinstance(result[field], list):
                result[field] = []
    
    return result

class TwilioNumberManager:
    """Manage Twilio phone number purchasing and assignment using MASTER ACCOUNT"""
    
    @staticmethod
    async def find_available_number(area_code: str = None):
        """Find and purchase available Twilio number using MASTER ACCOUNT"""
        try:
            # Search for available numbers using MASTER ACCOUNT
            if area_code:
                numbers = twilio_client.available_phone_numbers('US').local.list(
                    area_code=area_code,
                    sms_enabled=True,
                    voice_enabled=True,
                    limit=5
                )
            else:
                numbers = twilio_client.available_phone_numbers('US').local.list(
                    sms_enabled=True,
                    voice_enabled=True,
                    limit=5
                )
            
            if not numbers:
                # Fallback to number pool
                return await TwilioNumberManager.get_from_number_pool(area_code)
            
            # Purchase the first available number using MASTER ACCOUNT
            purchased_number = twilio_client.incoming_phone_numbers.create(
                phone_number=numbers[0].phone_number,
                sms_url="https://api.aimsghub.com/sms/webhook",
                sms_method="POST"
            )
            
            return {
                "success": True,
                "phone_number": purchased_number.phone_number,
                "sid": purchased_number.sid,
                "monthly_cost": 1.00,  # Standard cost
                "friendly_name": purchased_number.friendly_name
            }
            
        except TwilioRestException as e:
            logger.error(f"Twilio number purchase error: {e}")
            # Try fallback to number pool
            return await TwilioNumberManager.get_from_number_pool(area_code)
    
    @staticmethod
    async def get_from_number_pool(area_code: str = None):
        """Get number from pre-purchased pool"""
        try:
            numbers_collection = await get_twilio_numbers_collection()
            
            query = {"status": "available"}
            if area_code:
                query["area_code"] = area_code
            
            available_number = await numbers_collection.find_one(query)
            
            if available_number:
                # Update number status to assigned
                await numbers_collection.update_one(
                    {"_id": available_number["_id"]},
                    {"$set": {"status": "assigned", "assigned_at": datetime.now(timezone.utc)}}
                )
                
                return {
                    "success": True,
                    "phone_number": available_number["phone_number"],
                    "sid": available_number["phone_sid"],
                    "monthly_cost": available_number.get("monthly_cost", 1.00),
                    "from_pool": True
                }
            
            return {"success": False, "error": "No numbers available"}
            
        except Exception as e:
            logger.error(f"Error getting number from pool: {e}")
            return {"success": False, "error": str(e)}

async def create_twilio_subaccount(user_id: str, friendly_name: str):
    """Create Twilio sub-account for user under MASTER ACCOUNT"""
    if not twilio_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Twilio client not configured"
        )
    
    try:
        subaccount = twilio_client.api.accounts.create(
            friendly_name=friendly_name
        )
        
        return {
            "subaccount_sid": subaccount.sid,
            "subaccount_auth_token": subaccount.auth_token
        }
    except Exception as e:
        logger.error(f"Error creating Twilio subaccount: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create subaccount: {str(e)}"
        )

def get_twilio_subaccount_client(subaccount_sid: str, subaccount_auth_token: str):
    """Get Twilio client for specific sub-account"""
    return Client(subaccount_sid, subaccount_auth_token)

async def get_sms_user(user_id: str):
    """Get SMS user from MongoDB"""
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": user_id})
    return user

async def log_sms_send(user_id: str, to_number: str, from_number: str, message: str, message_id: str = None, status: str = "sent", cost: float = 0.0):
    """Log SMS sending activity"""
    try:
        sms_logs_collection = await get_sms_logs_collection()
        
        log_doc = {
            "user_id": user_id,
            "to_number": to_number,
            "from_number": from_number,
            "message": message,
            "sid": message_id,
            "status": status,
            "cost": cost,
            "timestamp": datetime.now(timezone.utc)
        }
        
        await sms_logs_collection.insert_one(log_doc)
        
    except Exception as e:
        logger.error(f"Error logging SMS send: {str(e)}")
        # Don't raise exception for logging errors

def clean_and_validate_phone_number(phone_str):
    """Clean and validate phone number with WhatsApp-style lenient validation"""
    if not phone_str:
        logger.debug(f"Phone string is empty: {phone_str}")
        return None
    
    # Convert to string and strip whitespace
    phone_str = str(phone_str).strip()
    
    if not phone_str:
        logger.debug(f"Phone string is empty after stripping: {phone_str}")
        return None
    
    logger.debug(f"Original phone number: {phone_str}")
    
    # Remove all non-digit characters except +
    phone_clean = re.sub(r'[^0-9+]', '', phone_str)
    
    if not phone_clean:
        logger.debug(f"Phone number empty after cleaning: {phone_str}")
        return None
    
    logger.debug(f"After cleaning non-digits: {phone_clean}")
    
    # Handle different formats
    if phone_clean.startswith('0'):
        # Local format like 01234567890 -> convert to international
        phone_clean = '+1' + phone_clean[1:]  # Convert to US format
        logger.debug(f"After converting 0 to +1: {phone_clean}")
    elif not phone_clean.startswith('+'):
        # Add + if missing (assuming it's a valid number without country code)
        phone_clean = '+' + phone_clean
        logger.debug(f"After adding +: {phone_clean}")
    
    # WhatsApp-style lenient validation - accept any reasonable phone number
    # Remove + for length calculation
    digits_only = re.sub(r'[^0-9]', '', phone_clean)
    
    if len(digits_only) >= 10 and len(digits_only) <= 15:
        logger.debug(f"Valid phone number: {phone_clean} (digits: {len(digits_only)})")
        return phone_clean
    else:
        logger.debug(f"Invalid phone number length: {phone_clean} (digits: {len(digits_only)})")
        return None

@router.post("/contacts/process-excel")
async def process_excel_contacts_sms(
    file: UploadFile = File(...),
    current_user_id: str = Depends(get_current_user_id)
):
    """Process Excel file and extract contacts for SMS - requires sms_marketing key"""
    try:
        # Validate file type
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            raise HTTPException(
                status_code=400, 
                detail="Only Excel files (.xlsx, .xls) are supported"
            )
        
        # Read Excel file
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # Convert column names to lowercase for case-insensitive matching
        df.columns = df.columns.str.lower()
        
        # Find phone number column
        phone_columns = ['number', 'phone', 'mobile', 'contact', 'phonenumber', 'whatsapp']
        phone_column = None
        
        for col in phone_columns:
            if col in df.columns:
                phone_column = col
                break
        
        if not phone_column:
            raise HTTPException(
                status_code=400, 
                detail="Could not find phone number column. Expected column names: " + ", ".join(phone_columns)
            )
        
        # Find name column (optional)
        name_columns = ['name', 'fullname', 'contactname', 'person']
        name_column = None
        
        for col in name_columns:
            if col in df.columns:
                name_column = col
                break
        
        # Extract contacts
        contacts = []
        for index, row in df.iterrows():
            try:
                phone_number = str(row[phone_column])
                if pd.notna(phone_number) and phone_number.strip():
                    cleaned_number = clean_and_validate_phone_number(phone_number)
                    if cleaned_number:
                        contact = {
                            "number": cleaned_number,
                            "name": str(row[name_column]).strip() if name_column and pd.notna(row[name_column]) else "",
                            "var1": "",
                            "var2": "",
                            "var3": ""
                        }
                        contacts.append(contact)
            except Exception as e:
                logger.warning(f"Error processing row {index}: {e}")
                continue
        
        if not contacts:
            raise HTTPException(
                status_code=400, 
                detail="No valid phone numbers found in the Excel file"
            )
        
        return {
            "success": True,
            "contacts": contacts,
            "total_contacts": len(contacts),
            "file_name": file.filename,
            "columns_found": {
                "phone_column": phone_column,
                "name_column": name_column if name_column else "Not found"
            }
        }
        
    except Exception as e:
        logger.error(f"Error processing Excel file: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing Excel file: {str(e)}"
        )

@router.post("/send", status_code=status.HTTP_200_OK)
async def send_sms_unified(
    request: Request,
    to_numbers: Optional[List[str]] = Body(None, description="Single number as string or multiple numbers as array"),
    contacts: Optional[List[dict]] = Body(None, description="Contacts with number and name"),
    excel_contacts: Optional[List[dict]] = Body(None, description="Excel contacts with number and name"),  # ADD THIS
    message: Optional[str] = Body(None, description="SMS message content"),
    campaign_name: Optional[str] = Body("SMS Campaign", description="Campaign name for tracking"),
    excel_file: UploadFile = File(None, description="Excel file with contacts"),
    current_user_id: str = Depends(get_current_user_id)
):
    """
    UNIFIED SMS SENDING ENDPOINT - Handles both single and bulk SMS
    Supports: 
    - Single number (string in to_numbers)
    - Multiple numbers (array in to_numbers) 
    - Contacts with names (contacts array)
    - Excel contacts (excel_contacts array) 
    - Excel file upload (excel_file)
    - JSON and form-data requests
    """
    if not twilio_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Twilio client not configured"
        )
        
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": current_user_id})
    
    if not user or not user.get("number_verified"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User number not verified. Please complete number registration first."
        )
    
    # Process recipients from all possible sources
    validated_contacts = []
    processed_numbers = set()  # Track duplicates
    
    # Try to get data from JSON body first, then form data
    content_type = request.headers.get("content-type", "")
    
    logger.info(f"Content-Type: {content_type}")
    logger.info(f"Received to_numbers: {to_numbers}")
    logger.info(f"Received contacts: {contacts}")
    logger.info(f"Received excel_contacts: {excel_contacts}")  # ADD THIS
    logger.info(f"Received message: {message}")
    logger.info(f"Received campaign_name: {campaign_name}")
    logger.info(f"Received excel_file: {excel_file}")
    
    if "application/json" in content_type:
        try:
            body_data = await request.json()
            logger.info(f"Raw JSON body: {body_data}")
            to_numbers = body_data.get("to_numbers", [])
            contacts = body_data.get("contacts", [])
            excel_contacts = body_data.get("excel_contacts", [])  # ADD THIS
            message = body_data.get("message", "")
            campaign_name = body_data.get("campaign_name", "SMS Campaign")
        except Exception as e:
            logger.error(f"Error parsing JSON body: {e}")
            # Don't raise exception, try to use provided parameters
    
    # Process to_numbers parameter (single string or array of strings)
    if to_numbers:
        logger.info(f"Processing to_numbers: {to_numbers}")
        if isinstance(to_numbers, str):
            # Handle single number as string
            to_numbers = [to_numbers]
        
        for number in to_numbers:
            if number and str(number).strip():
                logger.info(f"Processing number from to_numbers: {number}")
                cleaned_number = clean_and_validate_phone_number(number)
                if cleaned_number and cleaned_number not in processed_numbers:
                    processed_numbers.add(cleaned_number)
                    validated_contacts.append({
                        "number": cleaned_number,
                        "name": "",
                        "var1": "",
                        "var2": "",
                        "var3": ""
                    })
                    logger.info(f"Added validated number: {cleaned_number}")
                else:
                    logger.warning(f"Invalid or duplicate number: {number} -> {cleaned_number}")
    
    # Process contacts array (with names and other fields)
    if contacts:
        logger.info(f"Processing contacts: {contacts}")
        for contact in contacts:
            if isinstance(contact, dict) and contact.get("number"):
                logger.info(f"Processing contact: {contact}")
                cleaned_number = clean_and_validate_phone_number(contact["number"])
                if cleaned_number and cleaned_number not in processed_numbers:
                    processed_numbers.add(cleaned_number)
                    validated_contacts.append({
                        "number": cleaned_number,
                        "name": contact.get("name", ""),
                        "var1": contact.get("var1", ""),
                        "var2": contact.get("var2", ""),
                        "var3": contact.get("var3", "")
                    })
                    logger.info(f"Added validated contact: {cleaned_number}")
                else:
                    logger.warning(f"Invalid or duplicate contact number: {contact['number']} -> {cleaned_number}")
    
    # Process excel_contacts array (from frontend Excel processing) - ADD THIS SECTION
    if excel_contacts:
        logger.info(f"Processing excel_contacts: {excel_contacts}")
        for contact in excel_contacts:
            if isinstance(contact, dict) and contact.get("number"):
                logger.info(f"Processing excel_contact: {contact}")
                cleaned_number = clean_and_validate_phone_number(contact["number"])
                if cleaned_number and cleaned_number not in processed_numbers:
                    processed_numbers.add(cleaned_number)
                    validated_contacts.append({
                        "number": cleaned_number,
                        "name": contact.get("name", ""),
                        "var1": contact.get("var1", ""),
                        "var2": contact.get("var2", ""),
                        "var3": contact.get("var3", "")
                    })
                    logger.info(f"Added validated excel_contact: {cleaned_number}")
                else:
                    logger.warning(f"Invalid or duplicate excel_contact number: {contact['number']} -> {cleaned_number}")
    
    # Process Excel file if provided
    if excel_file and excel_file.filename:
        try:
            logger.info(f"Processing Excel file: {excel_file.filename}")
            # Validate file type
            if not excel_file.filename.lower().endswith(('.xlsx', '.xls')):
                raise HTTPException(
                    status_code=400, 
                    detail="Only Excel files (.xlsx, .xls) are supported"
                )
            
            excel_content = await excel_file.read()
            
            # Read Excel file
            if excel_file.filename.endswith('.xlsx'):
                df = pd.read_excel(io.BytesIO(excel_content), engine='openpyxl')
            else:
                df = pd.read_excel(io.BytesIO(excel_content))
            
            logger.info(f"Excel columns: {df.columns.tolist()}")
            
            # Convert column names to lowercase for case-insensitive matching
            df.columns = df.columns.str.lower()
            
            # Find phone number column
            phone_columns = ['number', 'phone', 'mobile', 'contact', 'phonenumber', 'whatsapp']
            phone_column = None
            
            for col in phone_columns:
                if col in df.columns:
                    phone_column = col
                    break
            
            if not phone_column:
                # Use first column if no phone column found
                phone_column = df.columns[0]
            
            # Find name column (optional)
            name_columns = ['name', 'fullname', 'contactname', 'person']
            name_column = None
            
            for col in name_columns:
                if col in df.columns:
                    name_column = col
                    break
            
            logger.info(f"Using phone column: {phone_column}, name column: {name_column}")
            
            # Extract contacts from Excel
            for index, row in df.iterrows():
                try:
                    phone_value = str(row[phone_column]) if pd.notna(row[phone_column]) else ""
                    if phone_value.strip():
                        logger.info(f"Processing Excel row {index}: {phone_value}")
                        cleaned_number = clean_and_validate_phone_number(phone_value)
                        if cleaned_number and cleaned_number not in processed_numbers:
                            processed_numbers.add(cleaned_number)
                            validated_contacts.append({
                                "number": cleaned_number,
                                "name": str(row[name_column]).strip() if name_column and pd.notna(row[name_column]) else "",
                                "var1": "",
                                "var2": "",
                                "var3": ""
                            })
                            logger.info(f"Added validated Excel number: {cleaned_number}")
                        else:
                            logger.warning(f"Invalid or duplicate Excel number: {phone_value} -> {cleaned_number}")
                except Exception as e:
                    logger.warning(f"Error processing Excel row {index}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Excel file: {str(e)}"
            )
    
    logger.info(f"Final validated contacts count: {len(validated_contacts)}")
    logger.info(f"Final validated contacts: {validated_contacts}")
    
    # Validate we have message and recipients
    if not message or not message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message content is required"
        )
    
    if len(validated_contacts) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid recipients found. Provide phone numbers, contacts, or upload an Excel file."
        )
    
    # Rest of the function remains the same...
    # Check SMS credits
    required_credits = len(validated_contacts)
    current_credits = user.get("sms_credits", 0)
    
    if current_credits < required_credits:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Insufficient SMS credits. Required: {required_credits}, Available: {current_credits}"
        )
    
    # Rate limiting check
    sms_logs_collection = await get_sms_logs_collection()
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_messages = await sms_logs_collection.count_documents({
        "user_id": current_user_id,
        "timestamp": {"$gte": hour_ago},
        "status": "sent"
    })
    
    if recent_messages + len(validated_contacts) > 100:  # 100 SMS per hour limit
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
            detail=f"Rate limit exceeded. You can send {100 - recent_messages} more SMS this hour."
        )
    
    # Get Twilio subaccount client
    subaccount_client = get_twilio_subaccount_client(
        user["subaccount_sid"],
        user["subaccount_auth_token"]
    )
    
    purchased_number = user["purchased_number"]
    successful_sends = 0
    failed_sends = 0
    message_sids = []
    batch_id = f"sms_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Send to all recipients
    for contact in validated_contacts:
        try:
            message_obj = subaccount_client.messages.create(
                body=message,
                from_=purchased_number,
                to=contact["number"]
            )
            
            # Log successful SMS
            await log_sms_send(
                user_id=current_user_id,
                to_number=contact["number"],
                from_number=purchased_number,
                message=message,
                message_id=message_obj.sid,
                status="sent",
                cost=0.0075
            )
            
            successful_sends += 1
            message_sids.append(message_obj.sid)
            
        except Exception as e:
            logger.error(f"Error sending SMS to {contact['number']}: {e}")
            
            # Log failed SMS
            await log_sms_send(
                user_id=current_user_id,
                to_number=contact["number"],
                from_number=purchased_number,
                message=message,
                status="failed",
                cost=0.0
            )
            
            failed_sends += 1
    
    # Update credits (only deduct successful sends)
    if successful_sends > 0:
        await sms_users_collection.update_one(
            {"user_id": current_user_id},
            {"$inc": {"sms_credits": -successful_sends}}
        )
    
    # Save campaign data
    sms_campaigns_collection = await get_sms_campaigns_collection()
    campaign = {
        "user_id": current_user_id,
        "campaign_name": campaign_name,
        "type": "sms",
        "status": "completed",
        "message_content": message,
        "contacts": validated_contacts,
        "sent_count": successful_sends,
        "failed_count": failed_sends,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc)
    }
    await sms_campaigns_collection.insert_one(campaign)
    
    # Prepare response
    response_data = {
        "success": True,
        "message": f"SMS sending completed",
        "total_recipients": len(validated_contacts),
        "successful": successful_sends,
        "failed": failed_sends,
        "remaining_credits": current_credits - successful_sends,
        "batch_id": batch_id,
        "campaign_name": campaign_name
    }
    
    # Include message SID for single recipient case
    if len(validated_contacts) == 1 and successful_sends == 1:
        response_data["sid"] = message_sids[0]
    
    return response_data

# Get SMS campaigns collection
async def get_sms_campaigns_collection():
    from services.database import mongodb
    return mongodb.db.sms_campaigns

@router.get("/campaigns")
async def get_sms_campaigns(
    current_user_id: str = Depends(get_current_user_id)
):
    """Get user's SMS campaigns - requires sms_marketing key"""
    try:
        campaigns_collection = await get_sms_campaigns_collection()
        
        campaigns_cursor = campaigns_collection.find({"user_id": current_user_id})
        campaigns = await campaigns_cursor.to_list(length=100)
        
        logger.info(f"Found {len(campaigns)} SMS campaigns for user {current_user_id}")
        
        # Safely convert all documents
        formatted_campaigns = []
        for campaign in campaigns:
            try:
                formatted_campaign = safe_convert_document(campaign)
                formatted_campaigns.append(formatted_campaign)
            except Exception as e:
                logger.error(f"Error converting campaign {campaign.get('_id')}: {e}")
                continue
        
        return formatted_campaigns
        
    except Exception as e:
        logger.error(f"Error in GET /sms/campaigns: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while fetching SMS campaigns"
        )

# ==================== EXISTING ROUTES (Updated with proper error handling) ====================

@router.post("/verify_business", status_code=status.HTTP_200_OK)
async def verify_business_profile(
    req: BusinessVerifyRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """Verify business profile before number assignment"""
    try:
        # Validate business information
        if not req.business_name or len(req.business_name) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Valid business name required"
            )
        
        if not req.business_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business type required"
            )
        
        # Store business verification data
        business_collection = await get_business_profiles_collection()
        await business_collection.update_one(
            {"user_id": current_user_id},
            {"$set": {
                "business_name": req.business_name,
                "business_type": req.business_type,
                "website": req.website,
                "business_email": req.business_email,
                "business_verified": True,
                "verified_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )
        
        return {"message": "Business profile verified successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Business verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying business: {str(e)}"
        )

@router.post("/register_number", status_code=status.HTTP_201_CREATED)
async def register_number(
    req: NumberRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """Enhanced number registration with AUTO-PURCHASE through MASTER ACCOUNT"""
    if not twilio_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Twilio client not configured"
        )
        
    try:
        # Fetch user from database
        users_collection = await get_users_collection()
        user = await users_collection.find_one({"_id": ObjectId(current_user_id)})
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Check if user has verified business profile
        business_collection = await get_business_profiles_collection()
        business_profile = await business_collection.find_one({"user_id": current_user_id})
        
        if not business_profile or not business_profile.get("business_verified"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business profile verification required"
            )
        
        username = user.get("username", "User")
        user_id_short = str(current_user_id)[-4:]
        friendly_name = f"{username}_{user_id_short}"[:30]
        
        # Step 1: AUTO-PURCHASE Twilio number for user using MASTER ACCOUNT
        number_data = await TwilioNumberManager.find_available_number(req.area_code)
        
        if not number_data["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=number_data.get("error", "Failed to get phone number")
            )
        
        # Step 2: Create Twilio sub-account for user
        subaccount_data = await create_twilio_subaccount(current_user_id, friendly_name)
        
        # Step 3: Create verification service
        subaccount_client = get_twilio_subaccount_client(
            subaccount_data["subaccount_sid"],
            subaccount_data["subaccount_auth_token"]
        )
        
        service = subaccount_client.verify.services.create(
            friendly_name=friendly_name
        )
        service_sid = service.sid
        
        # Send OTP to admin number for verification
        subaccount_client.verify.services(service_sid).verifications.create(
            to=req.admin_phone,  # Send OTP to admin phone for business verification
            channel="sms"
        )
        
        # Store comprehensive user data
        sms_users_collection = await get_sms_users_collection()
        await sms_users_collection.update_one(
            {"user_id": current_user_id},
            {"$set": {
                "purchased_number": number_data["phone_number"],
                "purchased_number_sid": number_data["sid"],
                "admin_phone": req.admin_phone,
                "verify_sid": service_sid,
                "subaccount_sid": subaccount_data["subaccount_sid"],
                "subaccount_auth_token": subaccount_data["subaccount_auth_token"],
                "friendly_name": friendly_name,
                "area_code": req.area_code,
                "number_purchased_at": datetime.now(timezone.utc),
                "monthly_cost": number_data.get("monthly_cost", 1.00),
                "sms_credits": 100,  # Initial credits
                "status": "pending_verification"
            }},
            upsert=True
        )
        
        return {
            "message": "Twilio number purchased and OTP sent to admin number",
            "purchased_number": number_data["phone_number"],
            "service_sid": service_sid,
            "monthly_cost": number_data.get("monthly_cost", 1.00)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in number registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error registering number: {str(e)}"
        )

@router.post("/verify_number", status_code=status.HTTP_200_OK)
async def verify_number(
    req: OTPVerifyRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """Verify admin phone number"""
    if not twilio_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Twilio client not configured"
        )
        
    sms_users_collection = await get_sms_users_collection()
    user = await sms_users_collection.find_one({"user_id": current_user_id})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    admin_phone = user["admin_phone"]
    service_sid = user["verify_sid"]
    
    subaccount_client = get_twilio_subaccount_client(
        user["subaccount_sid"],
        user["subaccount_auth_token"]
    )
    
    try:
        verification_check = subaccount_client.verify.services(service_sid).verification_checks.create(
            to=admin_phone,
            code=req.code
        )
        
        if verification_check.status == "approved":
            # Mark number as fully verified and active
            await sms_users_collection.update_one(
                {"user_id": current_user_id},
                {"$set": {
                    "number_verified": True,
                    "status": "active",
                    "verified_at": datetime.now(timezone.utc)
                }}
            )
            
            return {
                "message": "Number verified successfully",
                "purchased_number": user["purchased_number"],
                "status": "active",
                "sms_credits": user.get("sms_credits", 100)
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying number: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying number: {str(e)}"
        )

@router.get("/status")
async def get_user_sms_status(current_user_id: str = Depends(get_current_user_id)):
    """Get user SMS marketing status"""
    try:
        sms_users_collection = await get_sms_users_collection()
        user = await sms_users_collection.find_one({"user_id": current_user_id})
        
        if not user:
            return {
                "business_verified": False,
                "number_verified": False,
                "purchased_number": None,
                "sms_credits": 0,
                "status": "not_registered"
            }
        
        # Check business verification
        business_collection = await get_business_profiles_collection()
        business_profile = await business_collection.find_one({"user_id": current_user_id})
        
        return {
            "business_verified": business_profile and business_profile.get("business_verified", False),
            "number_verified": user.get("number_verified", False),
            "purchased_number": user.get("purchased_number"),
            "sms_credits": user.get("sms_credits", 0),
            "status": user.get("status", "inactive"),
            "monthly_cost": user.get("monthly_cost", 1.00)
        }
        
    except Exception as e:
        logger.error(f"Error getting SMS status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving SMS status: {str(e)}"
        )
       
@router.get("/logs")
async def get_sms_logs_endpoint(current_user_id: str = Depends(get_current_user_id), limit: int = 50):
    """Get SMS sending logs for user"""
    try:
        sms_logs_collection = await get_sms_logs_collection()
        cursor = sms_logs_collection.find({"user_id": current_user_id}).sort("timestamp", -1).limit(limit)
        logs = await cursor.to_list(length=limit)
        
        # Convert logs to JSON serializable format
        formatted_logs = []
        for log in logs:
            formatted_log = safe_convert_document(log)
            formatted_logs.append(formatted_log)
        
        return {
            "success": True,
            "user_id": current_user_id, 
            "logs": formatted_logs, 
            "total": len(formatted_logs)
        }
    except Exception as e:
        logger.error(f"Error retrieving SMS logs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving SMS logs: {str(e)}"
        )

@router.post("/webhook")
async def twilio_webhook(request: Request):
    """Handle Twilio webhook for delivery status"""
    try:
        data = await request.form()
        message_sid = data.get("MessageSid")
        sms_status = data.get("SmsStatus")
        
        sms_logs_collection = await get_sms_logs_collection()
        await sms_logs_collection.update_one(
            {"sid": message_sid},
            {"$set": {"status": sms_status, "updated_at": datetime.now(timezone.utc)}}
        )
        
        logger.info(f"SMS status update: {message_sid} -> {sms_status}")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing Twilio webhook: {str(e)}")
        return Response(status_code=status.HTTP_200_OK)

@router.post("/generate_api_key")
async def generate_sms_api_key(current_user_id: str = Depends(get_current_user_id)):
    """Generate SMS marketing API key for user"""
    try:
        api_keys_collection = await get_api_keys_collection()
        
        # Generate SMS scoped key
        key_data = APIKeyService.generate_scoped_key(current_user_id, "sms_marketing")
        
        # Store in database
        await api_keys_collection.update_one(
            {"user_id": ObjectId(current_user_id)},
            {
                "$set": {
                    f"keys.sms_marketing": {
                        "key": key_data["key"],
                        "expires_at": key_data["expires_at"],
                        "generated_at": key_data["generated_at"]
                    },
                    "user_id": ObjectId(current_user_id)
                }
            },
            upsert=True
        )
        
        return {
            "api_key": key_data["key"],
            "expires_at": key_data["expires_at"].isoformat(),
            "scope": "sms_marketing"
        }
        
    except Exception as e:
        logger.error(f"Error generating SMS API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate API key"
        )