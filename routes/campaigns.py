from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, UploadFile, File, Form, Header
from typing import Optional, List
import io
import pandas as pd
import re
import asyncio
from models.campaigns import IdeaInput, CampaignCreate
from models.marketing import SendEmailRequest, SMSRequest
from services.database import get_campaigns_collection, get_message_status_collection, get_users_collection
from services.auth import get_current_user
from services.whatsapp_service import send_whatsapp_message
from services.email_service import send_email_with_storage
from services.sms_service import send_sms
from services.generate_message import call_gemini_api
from bson import ObjectId
from datetime import datetime, timezone
import logging
from fastapi import Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

async def send_messages_background(numbers: List[str], message: str, user: dict, campaign_id: str):
    """Background task for sending WhatsApp messages"""
    message_status_collection = await get_message_status_collection()
    campaigns_collection = await get_campaigns_collection()
    
    for number in numbers:
        try:
            send_whatsapp_message(user['phone_number_id'], number, message, user['meta_api_key'])
            
            status_doc = {
                "campaign_id": ObjectId(campaign_id),
                "phone_number": number,
                "status": "sent",
                "sent_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            await message_status_collection.insert_one(status_doc)
            
            await asyncio.sleep(2.0)  # Rate limiting for WhatsApp
        except Exception as e:
            logger.error(f"Failed to send WhatsApp to {number}: {e}")
            continue
    
    # Update campaign status to completed
    await campaigns_collection.update_one(
        {"_id": ObjectId(campaign_id)},
        {"$set": {"status": "completed"}}
    )
    logger.info(f"Campaign {campaign_id} completed.")

async def send_bulk_emails_background(emails: List[str], subject: str, content: str, from_email: str, user_id: str):
    """Background task for sending bulk emails with rate limiting"""
    for email in emails:
        try:
            await send_email_with_storage(SendEmailRequest(
                user_id=user_id,
                to=email,
                subject=subject,
                from_email=from_email,
                content=content
            ))
            await asyncio.sleep(0.5)  # 2 emails/second
        except Exception as e:
            logger.error(f"Failed to send email to {email}: {e}")
            continue

async def send_bulk_sms_background(numbers: List[str], message: str, user_id: str):
    """Background task for sending bulk SMS with rate limiting"""
    for number in numbers:
        try:
            await send_sms(SMSRequest(
                user_id=user_id,
                to_number=number,
                message=message
            ))
            await asyncio.sleep(3.0)  # 20 SMS/minute
        except Exception as e:
            logger.error(f"Failed to send SMS to {number}: {e}")
            continue

@router.post("/generate-from-idea", status_code=200)
async def generate_message_from_idea(data: IdeaInput):
    system_prompt = "You are an expert WhatsApp marketing copywriter. Write a short, engaging, and friendly promotional message based on the user's idea. The message must be under 250 characters. Include placeholders like {name} for personalization. The response should only be the marketing message text, without any introductory phrases like 'Here is the message:' or quotes."
    user_query = f"Generate a WhatsApp marketing message for the following idea: {data.ai_idea}"
    
    generated_text = await call_gemini_api(system_prompt, user_query)
    return {"message": generated_text}

@router.post("/send", status_code=202)
async def send_bulk_message(
    campaign_name: str = Form(...),
    message: str = Form(...),
    campaign_type: str = Form(...),
    contacts_file: Optional[UploadFile] = File(None),
    manual_numbers: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    all_numbers = set()

    if manual_numbers:
        numbers_found = re.findall(r'\+?\d[\d\s-]*', manual_numbers)
        for num_str in numbers_found:
            cleaned_num = re.sub(r'[\s-]', '', num_str)
            if cleaned_num:
                all_numbers.add(cleaned_num)

    if contacts_file:
        if not contacts_file.filename.endswith(('.csv', '.xlsx')):
            raise HTTPException(status_code=400, detail="Invalid file type for contacts.")
        try:
            contents = await contacts_file.read()
            buffer = io.BytesIO(contents)
            if contacts_file.filename.endswith('.csv'):
                df = pd.read_csv(buffer)
            else:
                df = pd.read_excel(buffer)
                
            if 'number' not in df.columns:
                raise HTTPException(status_code=400, detail="File must contain a 'number' column.")
            for num in df['number'].dropna().astype(str):
                all_numbers.add(num.strip())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error processing file: {e}")

    if not all_numbers:
        raise HTTPException(status_code=400, detail="No contacts provided.")

    contact_list = list(all_numbers)
    
    campaigns_collection = await get_campaigns_collection()
    new_campaign = {
        "name": campaign_name,
        "message_template": message,
        "contact_count": len(contact_list),
        "owner_id": current_user["_id"],
        "status": "processing",
        "sent_at": datetime.now(timezone.utc)
    }
    
    result = await campaigns_collection.insert_one(new_campaign)
    campaign_id = str(result.inserted_id)
    
    # Start background task based on campaign type
    if campaign_type == "whatsapp":
        if not current_user.get('meta_api_key') or not current_user.get('phone_number_id'):
            raise HTTPException(status_code=400, detail="WhatsApp API credentials not set up.")
        background_tasks.add_task(send_messages_background, contact_list, message, current_user, campaign_id)
        
    elif campaign_type == "email":
        background_tasks.add_task(
            send_bulk_emails_background,
            contact_list, campaign_name, message, current_user['email'], str(current_user['_id'])
        )
        
    elif campaign_type == "sms":
        background_tasks.add_task(
            send_bulk_sms_background,
            contact_list, message, str(current_user['_id'])
        )
        
    else:
        raise HTTPException(status_code=400, detail="Invalid campaign type. Use 'whatsapp', 'email', or 'sms'")
    
    return {
        "message": "Campaign accepted and is being processed.", 
        "contacts_found": len(contact_list), 
        "campaign_id": campaign_id
    }