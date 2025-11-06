from fastapi import APIRouter, HTTPException, Request, Response
from models.marketing import EmailUserCreate, EmailUserUpdate, SendEmailRequest, SubuserCreate, DomainCreate, SendEmailModel
from services.database import get_email_users_collection, get_email_logs_collection
from config import SENDGRID_MASTER_KEY, SG_BASE
from sendgrid import SendGridAPIClient # pyright: ignore[reportMissingImports]
from sendgrid.helpers.mail import Mail # pyright: ignore[reportMissingImports]
import requests
from datetime import datetime, timezone
import logging
import secrets
import string

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email", tags=["Email Marketing"])

async def get_email_user(user_id: str):
    email_users_collection = await get_email_users_collection()
    user = await email_users_collection.find_one({"user_id": user_id})
    return user

def generate_random_password(length=16):
    """Generate secure random password for sub-user"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

async def create_sendgrid_subuser(email: str, username: str):
    """Create SendGrid sub-user account"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
    
    password = generate_random_password()
    
    payload = {
        "username": username,
        "email": email,
        "password": password,
        "ips": []  # Allow from all IPs
    }
    
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    
    resp = requests.post(f"{SG_BASE}/subusers", json=payload, headers=headers)
    if resp.status_code != 201:
        logger.error(f"SendGrid subuser creation failed: {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    
    return resp.json(), password

async def setup_sendgrid_domain(domain: str, subdomain: str, username: str):
    """Setup domain whitelabeling for sub-user"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
    
    payload = {
        "domain": domain,
        "subdomain": subdomain,
        "username": username,
        "automatic_security": True,
        "custom_spf": True,
        "default": False
    }
    
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    
    resp = requests.post(f"{SG_BASE}/whitelabel/domains", json=payload, headers=headers)
    if resp.status_code != 201:
        logger.error(f"SendGrid domain setup failed: {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    
    return resp.json()

async def generate_subuser_api_key(username: str, key_name: str = None):
    """Generate API key for sub-user"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
    
    key_name = key_name or f"{username}_api_key"
    payload = {
        "name": key_name,
        "scopes": ["mail.send"],
        "sample": username
    }
    
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    
    resp = requests.post(f"{SG_BASE}/api_keys", json=payload, headers=headers)
    if resp.status_code != 201:
        logger.error(f"SendGrid API key creation failed: {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    
    return resp.json()

async def create_email_user(user_data: EmailUserCreate):
    email_users_collection = await get_email_users_collection()
    
    existing_user = await email_users_collection.find_one({
        "$or": [
            {"user_id": user_data.user_id},
            {"username": user_data.username}
        ]
    })
    
    if existing_user:
        raise HTTPException(status_code=400, detail="User ID or username already exists")
    
    try:
        # Step 1: Create SendGrid sub-user
        subuser_data, password = await create_sendgrid_subuser(
            user_data.email, 
            user_data.username
        )
        
        # Step 2: Setup domain (using subdomain for users)
        domain_data = await setup_sendgrid_domain(
            domain=f"{user_data.username}.aimsghub.com",
            subdomain=user_data.username,
            username=user_data.username
        )
        
        # Step 3: Generate API key for sub-user
        api_key_data = await generate_subuser_api_key(user_data.username)
        
        user_doc = {
            "user_id": user_data.user_id,
            "username": user_data.username,
            "email": user_data.email,
            "subuser_id": subuser_data.get("username"),
            "domain": domain_data.get("domain"),
            "domain_id": domain_data.get("id"),
            "domain_verified": False,  # Will be verified after DNS setup
            "api_key": api_key_data.get("api_key"),  # Store the API key
            "dns_records": domain_data.get("dns", {}),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
            # Removed trial_remaining and trial_start
        }
        
        result = await email_users_collection.insert_one(user_doc)
        
        return {
            "user_id": str(result.inserted_id),
            "dns_records": domain_data.get("dns", {}),
            "domain_verification_required": True
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating email user: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating email user: {str(e)}")

async def update_email_user(user_id: str, update_data: EmailUserUpdate):
    email_users_collection = await get_email_users_collection()
    
    update_fields = {}
    
    if update_data.api_key is not None:
        update_fields["api_key"] = update_data.api_key
    
    if update_data.domain is not None:
        update_fields["domain"] = update_data.domain
    
    if update_data.subdomain is not None:
        update_fields["subdomain"] = update_data.subdomain
    
    if update_data.domain_id is not None:
        update_fields["domain_id"] = update_data.domain_id
    
    if update_data.domain_verified is not None:
        update_fields["domain_verified"] = update_data.domain_verified
    
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_fields["updated_at"] = datetime.now(timezone.utc)
    
    result = await email_users_collection.update_one(
        {"user_id": user_id},
        {"$set": update_fields}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return result.modified_count

async def log_email_send(user_id: str, to_email: str, from_email: str, subject: str, message_id: str = None, status: str = "sent"):
    email_logs_collection = await get_email_logs_collection()
    
    log_doc = {
        "user_id": user_id,
        "to_email": to_email,
        "from_email": from_email,
        "subject": subject,
        "message_id": message_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc)
    }
    
    await email_logs_collection.insert_one(log_doc)

@router.post("/create_user")
async def create_email_user_endpoint(data: EmailUserCreate):
    try:
        result = await create_email_user(data)
        return {
            "message": "Email user created successfully", 
            "user_id": result["user_id"],
            "dns_records": result["dns_records"],
            "next_steps": "Add the DNS records to your domain to complete setup"
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error creating email user: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating email user: {str(e)}")

@router.get("/user/{user_id}")
async def get_email_user_endpoint(user_id: str):
    user = await get_email_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Email user not found")
    
    # Remove sensitive data
    user.pop('api_key', None)
    return user

@router.put("/user/{user_id}")
async def update_email_user_endpoint(user_id: str, data: EmailUserUpdate):
    try:
        affected_rows = await update_email_user(user_id, data)
        return {"message": "Email user updated successfully", "affected_rows": affected_rows}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating email user: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating email user: {str(e)}")

@router.post("/verify_domain/{user_id}")
async def verify_user_domain(user_id: str):
    """Check domain verification status"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
    
    user = await get_email_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Email user not found")
    
    domain_id = user.get("domain_id")
    if not domain_id:
        raise HTTPException(status_code=400, detail="No domain configured for user")
    
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    
    # Check domain validation status
    resp = requests.get(f"{SG_BASE}/whitelabel/domains/{domain_id}", headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    
    domain_info = resp.json()
    is_valid = domain_info.get("valid", False)
    
    # Update user domain verification status
    if is_valid:
        await update_email_user(user_id, EmailUserUpdate(domain_verified=True))
    
    return {
        "domain_verified": is_valid,
        "validation_details": domain_info.get("validation_results", {})
    }

@router.post("/send")
async def send_email_with_storage(data: SendEmailRequest):
    user = await get_email_user(data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Email user not found")
    
    if not user.get("api_key"):
        raise HTTPException(status_code=400, detail="No API key configured for this user")
    
    if not user.get("domain_verified"):
        raise HTTPException(status_code=400, detail="Domain not verified. Please complete domain verification first.")
    
    try:
        message = Mail(
            from_email=data.from_email,
            to_emails=data.to,
            subject=data.subject,
            html_content=data.content
        )
        
        sg = SendGridAPIClient(user["api_key"])
        response = sg.send(message)
        
        await log_email_send(
            user_id=data.user_id,
            to_email=data.to,
            from_email=data.from_email,
            subject=data.subject,
            message_id=response.headers.get('X-Message-Id'),
            status="sent"
        )
        
        return {
            "status": "success", 
            "code": response.status_code,
            "message_id": response.headers.get('X-Message-Id')
        }
    except Exception as e:
        await log_email_send(
            user_id=data.user_id,
            to_email=data.to,
            from_email=data.from_email,
            subject=data.subject,
            status="failed"
        )
        logger.error(f"Error sending email: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/logs/{user_id}")
async def get_email_logs_endpoint(user_id: str, limit: int = 50):
    email_logs_collection = await get_email_logs_collection()
    cursor = email_logs_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    return {"user_id": user_id, "logs": logs, "total": len(logs)}

# Direct endpoints for manual operations (admin use)
@router.post("/create_subuser")
def create_subuser(data: SubuserCreate):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
        
    payload = data.dict()
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post(f"{SG_BASE}/subusers", json=payload, headers=headers)
    if resp.status_code != 201:
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    return resp.json()

@router.post("/add_domain")
def add_domain(data: DomainCreate):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
        
    payload = {
        "domain": data.domain,
        "subdomain": data.subdomain,
        "username": data.username,
        "automatic_security": True,
        "custom_spf": True,
        "default": False
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post(f"{SG_BASE}/whitelabel/domains", json=payload, headers=headers)
    if resp.status_code != 201:
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    return resp.json()

@router.post("/create_subuser_apikey/{username}")
def create_subuser_apikey(username: str, key_name: str = None):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(status_code=500, detail="SendGrid master key not configured")
        
    key_name = key_name or f"{username}_apikey"
    payload = {
        "name": key_name,
        "scopes": ["mail.send"]
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post(f"{SG_BASE}/api_keys", json=payload, headers=headers)
    if resp.status_code != 201:
        raise HTTPException(status_code=resp.status_code, detail=resp.json())
    return resp.json()

@router.post("/webhook")
async def sendgrid_webhook(request: Request):
    events = await request.json()
    for event in events:
        email = event.get("email")
        event_type = event.get("event")
        logger.info(f"Email event: {email}, {event_type}")
    return Response(status_code=200)