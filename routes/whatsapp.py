from fastapi import APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Form, Header
from services.database import get_users_collection, get_chat_history_collection
from services.auth import get_current_user, validate_api_key
from services.vector_store import load_vector_store_safely, close_vector_store, create_advanced_retriever
from services.generate_message import call_gemini_api
from services.whatsapp_service import send_whatsapp_message, send_whatsapp_media, send_whatsapp_interactive
from models.campaigns import IdeaInput
from routes.campaigns import generate_message_from_idea
from utils.embeddings import embedding_model
from config import META_API_VERIFY_TOKEN, WHATSAPP_API_URL, GROQ_API_KEY
from bson import ObjectId
import requests
import json
import asyncio
import logging
from datetime import datetime, timezone
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from typing import Optional, List
from fastapi import Query
from datetime import datetime, timezone, timedelta
from pymongo import UpdateOne

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])

# Get database collections
async def get_whatsapp_campaigns_collection():
    from services.database import mongodb
    return mongodb.db.whatsapp_campaigns

async def get_whatsapp_auto_replies_collection():
    from services.database import mongodb
    return mongodb.db.whatsapp_auto_replies

async def get_whatsapp_templates_collection():
    from services.database import mongodb
    return mongodb.db.whatsapp_templates

async def get_whatsapp_contacts_collection():
    from services.database import mongodb
    return mongodb.db.whatsapp_contacts

# WhatsApp Connection & Webhook Routes (Existing) - Keep JWT for these
def send_whatsapp_message(phone_number_id, to_number, message, access_token):
    """Send WhatsApp message via Meta API"""
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

@router.get("/connect")
async def whatsapp_connect(current_user: dict = Depends(get_current_user)):
    """Generate Meta Embedded Signup URL for WhatsApp Business API"""
    from config import META_APP_ID, META_REDIRECT_URI

    if not META_APP_ID or not META_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Meta app configuration missing")

    scopes = [
        "whatsapp_business_management",
        "whatsapp_business_messaging",
        "business_management"
    ]
    scope_param = "%2C".join(scopes)

    signup_url = (
        f"https://www.facebook.com/v19.0/dialog/oauth"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={META_REDIRECT_URI}"
        f"&scope={scope_param}"
        f"&state={str(current_user['_id'])}"
    )

    return {"signup_url": signup_url}

@router.get("/callback")
async def whatsapp_callback(request: Request):
    """Handle redirect after Meta Embedded Signup Flow"""
    from config import META_APP_ID, META_APP_SECRET, META_REDIRECT_URI
    
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    token_url = (
        f"https://graph.facebook.com/v19.0/oauth/access_token"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={META_REDIRECT_URI}"
        f"&client_secret={META_APP_SECRET}"
        f"&code={code}"
    )
    
    try:
        token_response = requests.get(token_url)
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get("access_token")
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to retrieve access token: {e}")

    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to retrieve access token")

    try:
        business_info = requests.get(
            f"https://graph.facebook.com/v19.0/me?fields=id,name&access_token={access_token}"
        ).json()

        waba_resp = requests.get(
            f"https://graph.facebook.com/v19.0/me/owned_whatsapp_business_accounts"
            f"?access_token={access_token}"
        ).json()

        waba_id = None
        phone_number_id = None

        if "data" in waba_resp and len(waba_resp["data"]) > 0:
            waba_id = waba_resp["data"][0]["id"]
            phone_resp = requests.get(
                f"https://graph.facebook.com/v19.0/{waba_id}/phone_numbers?access_token={access_token}"
            ).json()
            if "data" in phone_resp and len(phone_resp["data"]) > 0:
                phone_number_id = phone_resp["data"][0]["id"]

        users_collection = await get_users_collection()
        await users_collection.update_one(
            {"_id": ObjectId(state)},
            {"$set": {
                "meta_api_key": access_token,
                "phone_number_id": phone_number_id,
                "whatsapp_account_verified": True
            }}
        )

        return {
            "message": "WhatsApp account connected successfully!",
            "waba_id": waba_id,
            "phone_number_id": phone_number_id
        }
    except Exception as e:
        logger.error(f"Error in WhatsApp callback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during WhatsApp connection")

@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    data = await request.json()
    logger.info("Webhook received: %s", json.dumps(data, indent=2))
    
    try:
        message_obj = data['entry'][0]['changes'][0]['value']['messages'][0]
        from_number = message_obj['from']
        user_question = message_obj['text']['body']
        business_phone_id = data['entry'][0]['changes'][0]['value']['metadata']['phone_number_id']
    except (KeyError, IndexError):
        return Response(status_code=200)

    users_collection = await get_users_collection()
    user = await users_collection.find_one({"phone_number_id": business_phone_id})
    if not user or not user.get('vector_store_path'):
        return Response(status_code=200)

    chat_history_collection = await get_chat_history_collection()
    incoming_chat = {
        "user_id": user["_id"],
        "phone_number": from_number,
        "message": user_question,
        "is_from_user": True,
        "timestamp": datetime.now(timezone.utc)
    }
    await chat_history_collection.insert_one(incoming_chat)

    vector_store = None
    try:
        loop = asyncio.get_event_loop()
        vector_store = await loop.run_in_executor(
            None, load_vector_store_safely, user['vector_store_path']
        )
        
        advanced_retriever = create_advanced_retriever(vector_store, embedding_model)
        compressed_docs = await loop.run_in_executor(
            None,
            advanced_retriever.get_relevant_documents,
            user_question
        )
        
        docs_text = "\n\n".join([d.page_content for d in compressed_docs[:3]])
        logger.info(f"Retrieved {len(compressed_docs)} documents after compression")

        # Fetch last 5 chat messages for context
        last_msgs = await chat_history_collection.find(
            {"phone_number": from_number}
        ).sort("timestamp", -1).limit(5).to_list(length=5)
        
        # Build conversation context
        conversation_history = "\n".join([
            ("User: " + m["message"]) if m["is_from_user"] else ("Bot: " + m["message"])
            for m in reversed(last_msgs)
        ])

        combined_context = f"ShortConversation:\n{conversation_history}\n\nKnowledgeBase:\n{docs_text}".strip()

        if not GROQ_API_KEY:
            ai_response = "AI service is currently unavailable. Please try again later."
        else:
            chat_model = ChatGroq(api_key=GROQ_API_KEY, model="llama-3.3-70b-versatile", temperature=0.3)
            prompt = PromptTemplate.from_template("""
You are a intelligent bot that helps users based on the provided context. Your tone must be according to the whatsapp platform bot.
Context: {context}

Question: {question}

Instructions:
- Use the knowledge base content primarily to answer the question
- If the knowledge base doesn't contain relevant information, respond politely that you don't have that information
- Keep responses concise and helpful
- Maintain a friendly, professional tone
- Provide response in short and precise manner appropriate for WhatsApp Marketing Bot 
                                              
Note: Do not tell from  ur side that i do not find from theknowledge base provided instead just say an appology message that i do not have that information to that.
Do not use any vague or introduction message. Also if there is no context then just say that "Sorry, I donot have any information regarding this."

Answer:""")
            chain = prompt | chat_model
            response = await chain.ainvoke({"context": combined_context, "question": user_question})
            ai_response = response.content
        
    except Exception as e:
        logger.error(f"Error during advanced RAG processing: {e}")
        ai_response = "Sorry, I'm having trouble finding that information right now."
    finally:
        if vector_store:
            close_vector_store(vector_store)

    outgoing_chat = {
        "user_id": user["_id"],
        "phone_number": from_number,
        "message": ai_response,
        "is_from_user": False,
        "timestamp": datetime.now(timezone.utc)
    }
    await chat_history_collection.insert_one(outgoing_chat)

    if user.get('meta_api_key'):
        send_whatsapp_message(user['phone_number_id'], from_number, ai_response, user['meta_api_key'])
    
    return Response(status_code=200)

@router.get("/webhook")
async def verify_webhook(request: Request):
    if (request.query_params.get("hub.mode") == "subscribe" and 
        request.query_params.get("hub.verify_token") == META_API_VERIFY_TOKEN):
        return Response(content=request.query_params.get("hub.challenge"), status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

# ==================== UPDATED WHATSAPP MARKETING ROUTES WITH SINGLE API KEY ====================

# Single dependency function for all WhatsApp marketing features
async def require_whatsapp_marketing(x_api_key: str = Header(None)):
    return await validate_api_key("whatsapp_marketing", x_api_key)

# Campaign Management
@router.post("/campaigns")
async def create_campaign(
    campaign_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Create new WhatsApp campaign - requires whatsapp_marketing key"""
    campaigns_collection = await get_whatsapp_campaigns_collection()
    
    # Validate required fields
    if not campaign_data.get("name"):
        raise HTTPException(status_code=400, detail="Campaign name is required")
    
    if not campaign_data.get("type"):
        raise HTTPException(status_code=400, detail="Campaign type is required")
    
    campaign = {
        "user_id": ObjectId(current_user["_id"]),
        "name": campaign_data["name"],  
        "type": campaign_data["type"],  
        "status": campaign_data.get("status", "inactive"),
        "message_type": campaign_data.get("message_type", "Text Only"),
        "message_content": campaign_data.get("message_content", ""),
        "media_url": campaign_data.get("media_url", ""),
        "caption": campaign_data.get("caption", ""),
        "contacts": campaign_data.get("contacts", []),
        "sent_count": 0,
        "failed_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    result = await campaigns_collection.insert_one(campaign)
    return {"success": True, "campaign_id": str(result.inserted_id)}

from bson import ObjectId
import json

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
    datetime_fields = ["created_at", "updated_at", "timestamp", "sent_at"]
    for field in datetime_fields:
        if field in result and result[field]:
            if isinstance(result[field], datetime):
                result[field] = result[field].isoformat()
            elif result[field]:
                result[field] = str(result[field])
    
    # Ensure list fields are properly formatted
    list_fields = ["contacts", "instances", "buttons", "list_items"]
    for field in list_fields:
        if field in result:
            if not isinstance(result[field], list):
                result[field] = []
            # Ensure each contact has proper structure
            if field == "contacts":
                for i, contact in enumerate(result[field]):
                    if isinstance(contact, dict):
                        # Ensure contact has required fields
                        if "number" not in contact:
                            result[field][i] = {"number": "unknown", "name": "unknown"}
    
    return result

# Updated GET /whatsapp/campaigns endpoint
@router.get("/campaigns")
async def get_campaigns(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get user's WhatsApp campaigns - requires whatsapp_marketing key"""
    try:
        campaigns_collection = await get_whatsapp_campaigns_collection()
        
        campaigns_cursor = campaigns_collection.find({"user_id": current_user["_id"]})
        campaigns = await campaigns_cursor.to_list(length=100)
        
        logger.info(f"Found {len(campaigns)} campaigns for user {current_user['_id']}")
        
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
        logger.error(f"Error in GET /whatsapp/campaigns: {str(e)}")
        logger.exception(e)  # This will log the full traceback
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while fetching campaigns"
        )

@router.put("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    campaign_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Update WhatsApp campaign - requires whatsapp_marketing key"""
    campaigns_collection = await get_whatsapp_campaigns_collection()
    
    # Validate required fields
    if "name" not in campaign_data:
        raise HTTPException(status_code=400, detail="Campaign name is required")
    
    if "type" not in campaign_data:
        raise HTTPException(status_code=400, detail="Campaign type is required")
    
    # Build update fields - only include fields that are provided
    update_fields = {
        "name": campaign_data["name"],
        "type": campaign_data["type"],
        "updated_at": datetime.now(timezone.utc)
    }
    
    # Optional fields - only update if provided
    optional_fields = [
        "status", "message_type", "message_content", 
        "media_url", "caption", "contacts",
        "sent_count", "failed_count"
    ]
    
    for field in optional_fields:
        if field in campaign_data:
            update_fields[field] = campaign_data[field]
    
    result = await campaigns_collection.update_one(
        {"_id": ObjectId(campaign_id), "user_id": current_user["_id"]},
        {"$set": update_fields}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Campaign not found or no changes made")
    
    return {"success": True, "message": "Campaign updated successfully"}

@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: str, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Delete WhatsApp campaign - requires whatsapp_marketing key"""
    campaigns_collection = await get_whatsapp_campaigns_collection()
    
    result = await campaigns_collection.delete_one(
        {"_id": ObjectId(campaign_id), "user_id": current_user["_id"]}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return {"success": True}

@router.post("/knowledge-base/upload-replace")
async def upload_replace_knowledge_base(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Replace entire knowledge base with a single file (PDF, DOCX, TXT)"""
    from services.knowledge_base_service import update_replace_user_knowledge_base_service 
    from services.database import get_users_collection
    
    # Validate file type
    allowed_types = ['.pdf', '.docx', '.doc', '.txt']
    file_ext = '.' + file.filename.lower().split('.')[-1]
    
    if file_ext not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"File type not supported. Allowed: {', '.join(allowed_types)}"
        )
    
    users_collection = await get_users_collection()
    result = await update_replace_user_knowledge_base_service(current_user["_id"], file, users_collection)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

@router.get("/knowledge-base/status")
async def get_knowledge_base_status(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get current knowledge base status"""
    from services.database import get_users_collection
    
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"_id": ObjectId(current_user["_id"])})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "has_knowledge_base": bool(user.get('vector_store_path')),
        "current_file": user.get('knowledge_base_file'),
        "last_updated": user.get('knowledge_base_updated'),
        "documents_count": user.get('documents_count', 0)
    }

@router.delete("/knowledge-base/clear")
async def clear_knowledge_base(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Clear user's knowledge base"""
    from services.database import get_users_collection
    import shutil
    import os
    
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"_id": ObjectId(current_user["_id"])})
    
    if user and user.get('vector_store_path'):
        # Delete vector store files
        vector_store_dir = os.path.dirname(user['vector_store_path'])
        if os.path.exists(vector_store_dir):
            shutil.rmtree(vector_store_dir)
    
    # Update user in database
    await users_collection.update_one(
        {"_id": ObjectId(current_user["_id"])},
        {"$unset": {
            "vector_store_path": "",
            "knowledge_base_file": "",
            "knowledge_base_updated": "",
            "documents_count": ""
        }}
    )
    
    return {"success": True, "message": "Knowledge base cleared successfully"}

# Auto Reply Management
@router.post("/auto-replies")
async def create_auto_reply(
    auto_reply_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Create auto-reply rule - requires whatsapp_marketing key"""
    auto_replies_collection = await get_whatsapp_auto_replies_collection()

    if not auto_reply_data.get("keyword"):
        raise HTTPException(status_code=400, detail="Keyword is required")
    
    if not auto_reply_data.get("message_type"):
        raise HTTPException(status_code=400, detail="Message type is required")
    
    if not auto_reply_data.get("message_content"):
        raise HTTPException(status_code=400, detail="Message content is required")
    
    auto_reply = {
        "user_id": ObjectId(current_user["_id"]),
        "keyword": auto_reply_data["keyword"],
        "instances": auto_reply_data.get("instances", []),
        "message_type": auto_reply_data["message_type"],
        "message_content": auto_reply_data["message_content"],
        "media_url": auto_reply_data.get("media_url", ""),
        "caption": auto_reply_data.get("caption", ""),
        "buttons": auto_reply_data.get("buttons", []),
        "list_items": auto_reply_data.get("list_items", []),
        "poll_data": auto_reply_data.get("poll_data"),
        "is_active": True,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await auto_replies_collection.insert_one(auto_reply)
    return {"success": True, "auto_reply_id": str(result.inserted_id)}

@router.get("/auto-replies")
async def get_auto_replies(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get user's auto-reply rules - requires whatsapp_marketing key"""
    try:
        auto_replies_collection = await get_whatsapp_auto_replies_collection()
        
        auto_replies_cursor = auto_replies_collection.find({"user_id": current_user["_id"]})
        auto_replies = await auto_replies_cursor.to_list(length=100)
        
        logger.info(f"Found {len(auto_replies)} auto-replies for user {current_user['_id']}")
        
        # Safely convert all documents
        formatted_replies = []
        for reply in auto_replies:
            try:
                formatted_reply = safe_convert_document(reply)
                formatted_replies.append(formatted_reply)
            except Exception as e:
                logger.error(f"Error converting auto-reply {reply.get('_id')}: {e}")
                continue
        
        return formatted_replies
        
    except Exception as e:
        logger.error(f"Error in GET /whatsapp/auto-replies: {str(e)}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while fetching auto-replies"
        )
    
@router.put("/auto-replies/{auto_reply_id}")
async def update_auto_reply(
    auto_reply_id: str, 
    auto_reply_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Update auto-reply rule - requires whatsapp_marketing key"""
    auto_replies_collection = await get_whatsapp_auto_replies_collection()
    
    result = await auto_replies_collection.update_one(
        {"_id": ObjectId(auto_reply_id), "user_id": current_user["_id"]},
        {"$set": {**auto_reply_data, "updated_at": datetime.now(timezone.utc)}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Auto-reply not found")
    
    return {"success": True}

@router.delete("/auto-replies/{auto_reply_id}")
async def delete_auto_reply(
    auto_reply_id: str, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Delete auto-reply rule - requires whatsapp_marketing key"""
    auto_replies_collection = await get_whatsapp_auto_replies_collection()
    
    result = await auto_replies_collection.delete_one(
        {"_id": ObjectId(auto_reply_id), "user_id": current_user["_id"]}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Auto-reply not found")
    
    return {"success": True}

# Message Sending
@router.post("/send-message")
async def send_bulk_message(
    message_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Send bulk WhatsApp messages with only Template and AI options - requires whatsapp_marketing key"""
    
    # Extract common fields
    contacts = message_data.get("contacts", [])
    campaign_name = message_data.get("campaign_name", "")
    message_type = message_data.get("message_type", "")
    
    # Validate required fields
    if not contacts:
        raise HTTPException(status_code=400, detail="Contacts are required")
    
    if not campaign_name:
        raise HTTPException(status_code=400, detail="Campaign name is required")
    
    if not message_type:
        raise HTTPException(status_code=400, detail="Message type is required")
    
    # Validate message_type values
    valid_message_types = ["Text Only", "Text with Media", "Media Only"]
    if message_type not in valid_message_types:
        raise HTTPException(status_code=400, detail="Message type must be one of: " + ", ".join(valid_message_types))
    
    # Determine message source: template or AI-generated
    message_source = message_data.get("message_source")
    
    if message_source not in ["template", "ai"]:
        raise HTTPException(status_code=400, detail="Message source must be either 'template' or 'ai'")
    
    # Instance selection (optional)
    instance_id = message_data.get("instance_id")
    if instance_id:
        phone_number_id = instance_id
    else:
        if not current_user.get('phone_number_id'):
            raise HTTPException(status_code=400, detail="WhatsApp not connected")
        phone_number_id = current_user['phone_number_id']
    
    if not current_user.get('meta_api_key'):
        raise HTTPException(status_code=400, detail="WhatsApp API key not found")
    
    message_content = ""
    media_url = message_data.get("media_url", "")
    caption = message_data.get("caption", "")
    
    # Handle different message sources
    if message_source == "template":
        # Get message from selected template
        template_id = message_data.get("template_id")
        if not template_id:
            raise HTTPException(status_code=400, detail="Template ID is required when using template source")
        
        templates_collection = await get_whatsapp_templates_collection()
        template = await templates_collection.find_one({
            "_id": ObjectId(template_id), 
            "user_id": current_user["_id"]
        })
        
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        
        message_content = template.get("content", "")
        media_url = media_url or template.get("media_url", "")
        caption = caption or template.get("caption", "")
        
    elif message_source == "ai":
        # For AI, use the PRE-GENERATED message content directly
        message_content = message_data.get("message_content", "")
        ai_idea = message_data.get("ai_idea", "")  # Original idea for record keeping
        
        if not message_content:
            raise HTTPException(status_code=400, detail="AI message content is required")
        
    # Validation based on message type
    if not message_content:
        raise HTTPException(status_code=400, detail="Message content is required")
    
    if message_type in ["Text with Media", "Media Only"] and not media_url:
        raise HTTPException(status_code=400, detail="Media URL is required for media messages")
    
    # Process sending messages (same as before)
    results = []
    successful_sends = 0
    failed_sends = 0
    
    for contact in contacts:
        try:
            if message_type == "Text Only":
                response = send_whatsapp_message(
                    phone_number_id=phone_number_id,
                    to_number=contact["number"],
                    message=message_content,
                    access_token=current_user['meta_api_key']
                )
            elif message_type in ["Text with Media", "Media Only"]:
                response = send_whatsapp_media(
                    phone_number_id=phone_number_id,
                    to_number=contact["number"],
                    media_url=media_url,
                    caption=caption if message_type == "Text with Media" else "",
                    access_token=current_user['meta_api_key']
                )
            else:
                response = send_whatsapp_message(
                    phone_number_id=phone_number_id,
                    to_number=contact["number"],
                    message=message_content,
                    access_token=current_user['meta_api_key']
                )
            
            if response:
                successful_sends += 1
                results.append({"contact": contact["number"], "status": "sent"})
            else:
                failed_sends += 1
                results.append({"contact": contact["number"], "status": "failed"})
                
        except Exception as e:
            logger.error(f"Failed to send to {contact['number']}: {e}")
            failed_sends += 1
            results.append({"contact": contact["number"], "status": "failed"})
    
    # Save campaign
    campaigns_collection = await get_whatsapp_campaigns_collection()
    campaign = {
        "user_id": current_user["_id"],
        "name": campaign_name,
        "type": "broadcast",
        "status": "completed",
        "message_source": message_source,
        "message_type": message_type,
        "message_content": message_content,
        "media_url": media_url,
        "caption": caption,
        "template_id": message_data.get("template_id"),
        "ai_idea": message_data.get("ai_idea"),  # Store original idea
        "instance_id": instance_id,
        "contacts": contacts,
        "sent_count": successful_sends,
        "failed_count": failed_sends,
        "created_at": datetime.now(timezone.utc)
    }
    await campaigns_collection.insert_one(campaign)
    
    return {
        "success": True,
        "sent_count": successful_sends,
        "failed_count": failed_sends,
        "message_source": message_source,
        "instance_used": instance_id,
        "campaign_name": campaign_name,
        "message_content": message_content,  
        "message_type": message_type,        
        "media_used": media_url if media_url else None,  
        "results": results
    }

# Statistics and Analytics
@router.get("/statistics/overview")
async def get_statistics_overview(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get WhatsApp marketing overview statistics - requires whatsapp_marketing key"""
    campaigns_collection = await get_whatsapp_campaigns_collection()
    
    # Calculate totals
    total_campaigns = await campaigns_collection.count_documents({"user_id": current_user["_id"]})
    
    pipeline = [
        {"$match": {"user_id": current_user["_id"]}},
        {"$group": {
            "_id": None,
            "total_messages": {"$sum": {"$add": ["$sent_count", "$failed_count"]}},
            "sent_messages": {"$sum": "$sent_count"},
            "failed_messages": {"$sum": "$failed_count"}
        }}
    ]
    
    stats_cursor = await campaigns_collection.aggregate(pipeline).to_list(length=1)
    stats = stats_cursor[0] if stats_cursor else {}
    
    return {
        "totalMessages": stats.get("total_messages", 0),
        "sentMessages": stats.get("sent_messages", 0),
        "pendingMessages": 0,  # You can calculate based on status
        "activeCampaigns": await campaigns_collection.count_documents({
            "user_id": current_user["_id"], 
            "status": "active"
        }),
        "subscribers": 0,  # You might want to track this separately
        "responseRate": 68  # Mock data for now
    }

@router.get("/send-message")
async def get_message_history(
    current_user: dict = Depends(require_whatsapp_marketing),
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    campaign_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    days: Optional[int] = Query(30, ge=1, le=365)
):
    """Get message sending history with filtering - requires whatsapp_marketing key"""
    try:
        campaigns_collection = await get_whatsapp_campaigns_collection()
        
        # Convert user_id to ObjectId for query
        user_object_id = ObjectId(current_user["_id"])
        
        # Build filter query
        filter_query = {"user_id": user_object_id, "type": "broadcast"}
        
        if campaign_name:
            filter_query["name"] = {"$regex": campaign_name, "$options": "i"}
        
        if status:
            filter_query["status"] = status
        
        # Calculate date range if days provided
        if days:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)
            filter_query["created_at"] = {"$gte": start_date}
        
        # Get total count for pagination
        total_count = await campaigns_collection.count_documents(filter_query)
        
        # Get campaigns with pagination and sorting (newest first)
        campaigns_cursor = campaigns_collection.find(filter_query).sort("created_at", -1).skip(skip).limit(limit)
        campaigns = await campaigns_cursor.to_list(length=limit)
        
        logger.info(f"Found {len(campaigns)} message campaigns for user {current_user['_id']}")
        
        # Safely convert all documents
        formatted_campaigns = []
        for campaign in campaigns:
            try:
                formatted_campaign = safe_convert_document(campaign)
                
                # Calculate additional fields for frontend
                total_contacts = len(formatted_campaign.get("contacts", []))
                success_rate = 0
                if formatted_campaign.get("sent_count", 0) + formatted_campaign.get("failed_count", 0) > 0:
                    success_rate = (formatted_campaign.get("sent_count", 0) / 
                                  (formatted_campaign.get("sent_count", 0) + formatted_campaign.get("failed_count", 0))) * 100
                
                formatted_campaign["total_contacts"] = total_contacts
                formatted_campaign["success_rate"] = round(success_rate, 2)
                
                formatted_campaigns.append(formatted_campaign)
            except Exception as e:
                logger.error(f"Error converting campaign {campaign.get('_id')}: {e}")
                continue
        
        return {
            "success": True,
            "messages": formatted_campaigns,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "skip": skip,
                "has_more": (skip + limit) < total_count
            }
        }
        
    except Exception as e:
        logger.error(f"Error in GET /whatsapp/send-message: {str(e)}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while fetching message history"
        )

@router.get("/statistics/message-trends")
async def get_message_trends(
    days: int = 7, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get message trends for charts - requires whatsapp_marketing key"""
    # Mock data matching frontend structure - replace with actual aggregation
    return [
        {"date": "Oct 01", "messages": 320},
        {"date": "Oct 02", "messages": 410},
        {"date": "Oct 03", "messages": 380},
        {"date": "Oct 04", "messages": 470},
        {"date": "Oct 05", "messages": 520},
        {"date": "Oct 06", "messages": 610},
        {"date": "Oct 07", "messages": 560},
    ]

# Template Management
@router.post("/templates")
async def create_template(
    template_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Create WhatsApp template - requires whatsapp_marketing key"""
    templates_collection = await get_whatsapp_templates_collection()
    
    template = {
        "user_id": ObjectId(current_user["_id"]),
        "name": template_data["name"],
        "type": template_data["type"],
        "content": template_data["content"],
        "media_url": template_data.get("media_url", ""),
        "caption": template_data.get("caption", ""),
        "buttons": template_data.get("buttons", []),
        "list_items": template_data.get("list_items", []),
        "poll_data": template_data.get("poll_data"),
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await templates_collection.insert_one(template)
    return {"success": True, "template_id": str(result.inserted_id)}

@router.get("/templates")
async def get_templates(
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get user's WhatsApp templates - requires whatsapp_marketing key"""
    try:
        templates_collection = await get_whatsapp_templates_collection()
        
        templates_cursor = templates_collection.find({"user_id": current_user["_id"]})
        templates = await templates_cursor.to_list(length=100)
        
        logger.info(f"Found {len(templates)} templates for user {current_user['_id']}")
        
        # Safely convert all documents
        formatted_templates = []
        for template in templates:
            try:
                formatted_template = safe_convert_document(template)
                formatted_templates.append(formatted_template)
            except Exception as e:
                logger.error(f"Error converting template {template.get('_id')}: {e}")
                continue
        
        return formatted_templates
        
    except Exception as e:
        logger.error(f"Error in GET /whatsapp/templates: {str(e)}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while fetching templates"
        )
        
@router.put("/templates/{template_id}")
async def update_template(
    template_id: str, 
    template_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Update WhatsApp template - requires whatsapp_marketing key"""
    templates_collection = await get_whatsapp_templates_collection()
    
    result = await templates_collection.update_one(
        {"_id": ObjectId(template_id), "user_id": current_user["_id"]},
        {"$set": {**template_data, "updated_at": datetime.now(timezone.utc)}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {"success": True}

@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Delete WhatsApp template - requires whatsapp_marketing key"""
    templates_collection = await get_whatsapp_templates_collection()
    
    result = await templates_collection.delete_one(
        {"_id": ObjectId(template_id), "user_id": current_user["_id"]}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {"success": True}

# Contact Management
@router.post("/contacts/upload")
async def upload_contacts(
    contacts_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Upload and validate contacts - requires whatsapp_marketing key"""
    contacts_collection = await get_whatsapp_contacts_collection()
    
    operations = []
    processed_contacts = []
    duplicate_updates = 0
    new_inserts = 0
    
    for contact in contacts_data["contacts"]:
        try:
            number = contact["number"].strip()
            if not number:
                continue
                
            # Create filter and update operation
            filter_query = {
                "user_id": ObjectId(current_user["_id"]),
                "number": number
            }
            
            update_data = {
                "$set": {
                    "name": contact.get("name", ""),
                    "var1": contact.get("var1", ""),
                    "var2": contact.get("var2", ""),
                    "var3": contact.get("var3", ""),
                    "country_code": contact.get("country_code", "+91"),
                    "updated_at": datetime.now(timezone.utc)
                },
                "$setOnInsert": {
                    "is_valid": True,
                    "created_at": datetime.now(timezone.utc)
                }
            }
            
            operations.append(UpdateOne(filter_query, update_data, upsert=True))
            processed_contacts.append(contact)
            
        except Exception as e:
            logger.error(f"Error processing contact {contact}: {e}")
    
    # Execute bulk operations
    if operations:
        try:
            result = await contacts_collection.bulk_write(operations, ordered=False)
            new_inserts = result.upserted_count
            duplicate_updates = result.modified_count
            
        except Exception as e:
            logger.error(f"Error in bulk write: {e}")
            # Fallback to individual operations
            new_inserts = 0
            duplicate_updates = 0
            for operation in operations:
                try:
                    await contacts_collection.update_one(
                        operation._filter, 
                        operation._update, 
                        upsert=True
                    )
                    # We can't easily distinguish between insert vs update in fallback
                    new_inserts += 1
                except Exception as single_error:
                    logger.error(f"Error in individual upsert: {single_error}")
    
    return {
        "success": True,
        "inserted_count": new_inserts,
        "updated_count": duplicate_updates,
        "total_processed": len(processed_contacts),
        "summary": {
            "new_contacts": new_inserts,
            "updated_contacts": duplicate_updates,
            "total_operations": len(processed_contacts)
        }
    }

@router.post("/contacts/check-duplicates")
async def check_duplicate_contacts(
    contacts_data: dict, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Check which contacts already exist before uploading"""
    contacts_collection = await get_whatsapp_contacts_collection()
    
    existing_contacts = []
    new_contacts = []
    
    for contact in contacts_data["contacts"]:
        number = contact["number"].strip()
        if not number:
            continue
            
        existing_contact = await contacts_collection.find_one({
            "user_id": current_user["_id"],
            "number": number
        })
        
        if existing_contact:
            existing_contacts.append({
                "number": number,
                "name": contact.get("name", ""),
                "existing_data": safe_convert_document(existing_contact)
            })
        else:
            new_contacts.append(contact)
    
    return {
        "success": True,
        "existing_count": len(existing_contacts),
        "new_count": len(new_contacts),
        "existing_contacts": existing_contacts,
        "new_contacts": new_contacts
    }

@router.get("/reports/campaign/{campaign_id}")
async def get_campaign_report(
    campaign_id: str, 
    current_user: dict = Depends(require_whatsapp_marketing)
):
    """Get detailed campaign report - requires whatsapp_marketing key"""
    campaigns_collection = await get_whatsapp_campaigns_collection()
    
    campaign = await campaigns_collection.find_one({
        "_id": ObjectId(campaign_id), 
        "user_id": current_user["_id"]
    })
    
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    # Format campaign for frontend
    campaign["_id"] = str(campaign["_id"])
    campaign["created_at"] = campaign["created_at"].isoformat() if campaign.get("created_at") else None
    
    return {
        "totalMessages": campaign.get("sent_count", 0) + campaign.get("failed_count", 0),
        "sentMessages": campaign.get("sent_count", 0),
        "pendingMessages": 0,
        "pausedMessages": 0,
        "cancelledMessages": 0,
        "failedMessages": campaign.get("failed_count", 0),
        "invalidNumbers": 0,
        "nonWhatsappNumbers": 0,
        "messageList": [
            {
                "number": contact.get("number", ""),
                "instance": "Demo",  # You can store instance info
                "instanceNumber": current_user.get('phone_number_id', ''),
                "messageType": campaign.get("message_type", "Text"),
                "preview": "No",
                "status": "Sent",
                "createdAt": campaign.get("created_at", ""),
                "sentAt": campaign.get("created_at", "")
            }
            for contact in campaign.get("contacts", [])[:10]  # Limit for demo
        ]
    }

# Keep existing debug endpoints with JWT for testing
@router.get("/debug-token")
async def debug_token(current_user: dict = Depends(get_current_user)):
    """Debug endpoint to check if token is valid"""
    return {
        "status": "Token is valid!",
        "user_id": str(current_user["_id"]),
        "email": current_user["email"],
        "whatsapp_connected": current_user.get("whatsapp_account_verified", False)
    }

@router.get("/debug-headers")
async def debug_headers(request: Request):
    """Debug endpoint to check what headers are received"""
    headers = dict(request.headers)
    authorization = request.headers.get("authorization") or request.headers.get("Authorization")
    api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    
    return {
        "received_headers": list(headers.keys()),
        "authorization_header": authorization,
        "api_key_header": api_key,
        "content_type": request.headers.get("content-type"),
        "authorization_parts": authorization.split() if authorization else None
    }

# New endpoint to test API key validation
@router.get("/test-api-key")
async def test_api_key(current_user: dict = Depends(require_whatsapp_marketing)):
    """Test endpoint to verify API key validation is working"""
    return {
        "status": "API key validation successful!",
        "user_id": str(current_user["_id"]),
        "email": current_user["email"],
        "message": "Your API key has the required permissions for all WhatsApp marketing operations."
    }