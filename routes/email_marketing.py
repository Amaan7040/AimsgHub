from fastapi import APIRouter, HTTPException, Request, Response, status, Depends, Header
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

# Import API key service for authentication
from services.api_key_service import APIKeyService
from services.database import get_api_keys_collection

async def get_current_user_from_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Get current user from email_marketing API key
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required"
        )
    
    # Validate the API key with email_marketing scope
    validation_result = APIKeyService.validate_api_key(x_api_key, "email_marketing")
    
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="SendGrid master key not configured"
        )
    
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
    
    try:
        resp = requests.post(f"{SG_BASE}/subusers", json=payload, headers=headers)
        if resp.status_code != 201:
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"SendGrid subuser creation failed - Status: {resp.status_code}, Error: {error_detail}")
            
            # Map SendGrid errors to appropriate HTTP status codes
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            elif resp.status_code == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="SendGrid access forbidden"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid API error: {error_detail}"
                )
        
        return resp.json(), password
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during SendGrid subuser creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in SendGrid subuser creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

async def setup_sendgrid_domain(domain: str, subdomain: str, username: str):
    """Setup domain whitelabeling for sub-user"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
    
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
    
    try:
        resp = requests.post(f"{SG_BASE}/whitelabel/domains", json=payload, headers=headers)
        if resp.status_code != 201:
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"SendGrid domain setup failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid domain setup error: {error_detail}"
                )
        
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during SendGrid domain setup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in SendGrid domain setup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

async def generate_subuser_api_key(username: str, key_name: str = None):
    """Generate API key for sub-user"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
    
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
    
    try:
        resp = requests.post(f"{SG_BASE}/api_keys", json=payload, headers=headers)
        if resp.status_code != 201:
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"SendGrid API key creation failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid API key creation error: {error_detail}"
                )
        
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during SendGrid API key creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in SendGrid API key creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

async def create_email_user(user_id: str, user_data: EmailUserCreate):
    email_users_collection = await get_email_users_collection()
    
    existing_user = await email_users_collection.find_one({
        "$or": [
            {"user_id": user_id},
            {"username": user_data.username}
        ]
    })
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID or username already exists"
        )
    
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
            "user_id": user_id,
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
        logger.error(f"Error creating email user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating email user: {str(e)}"
        )

async def update_email_user(user_id: str, update_data: EmailUserUpdate):
    try:
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )
        
        update_fields["updated_at"] = datetime.now(timezone.utc)
        
        result = await email_users_collection.update_one(
            {"user_id": user_id},
            {"$set": update_fields}
        )
        
        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return result.modified_count
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating email user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating email user: {str(e)}"
        )

async def log_email_send(user_id: str, to_email: str, from_email: str, subject: str, message_id: str = None, status: str = "sent"):
    try:
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
        
    except Exception as e:
        logger.error(f"Error logging email send: {str(e)}")
        # Don't raise exception for logging errors as they shouldn't break the main flow

@router.post("/create_user", status_code=status.HTTP_201_CREATED)
async def create_email_user_endpoint(data: EmailUserCreate, current_user_id: str = Depends(get_current_user_id)):
    try:
        # Pass user_id as separate parameter to create_email_user
        result = await create_email_user(current_user_id, data)
        return {
            "message": "Email user created successfully", 
            "user_id": result["user_id"],
            "dns_records": result["dns_records"],
            "next_steps": "Add the DNS records to your domain to complete setup"
        }
    except HTTPException as e:
        logger.error(f"HTTP error in create_email_user_endpoint: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error in create_email_user_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating email user: {str(e)}"
        )

@router.get("/user")
async def get_email_user_endpoint(current_user_id: str = Depends(get_current_user_id)):
    try:
        user = await get_email_user(current_user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email user not found"
            )
        
        # Remove sensitive data
        user.pop('api_key', None)
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting email user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving user: {str(e)}"
        )

@router.put("/user")
async def update_email_user_endpoint(data: EmailUserUpdate, current_user_id: str = Depends(get_current_user_id)):
    try:
        affected_rows = await update_email_user(current_user_id, data)
        return {
            "message": "Email user updated successfully", 
            "affected_rows": affected_rows
        }
    except HTTPException as e:
        logger.error(f"HTTP error in update_email_user_endpoint: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error in update_email_user_endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating email user: {str(e)}"
        )

@router.post("/verify_domain")
async def verify_user_domain(current_user_id: str = Depends(get_current_user_id)):
    """Check domain verification status"""
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
    
    try:
        user = await get_email_user(current_user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email user not found"
            )
        
        domain_id = user.get("domain_id")
        if not domain_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No domain configured for user"
            )
        
        headers = {
            "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        
        # Check domain validation status
        resp = requests.get(f"{SG_BASE}/whitelabel/domains/{domain_id}", headers=headers)
        if resp.status_code != 200:
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"SendGrid domain verification check failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Domain not found in SendGrid"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid domain verification error: {error_detail}"
                )
        
        domain_info = resp.json()
        is_valid = domain_info.get("valid", False)
        
        # Update user domain verification status
        if is_valid:
            await update_email_user(current_user_id, EmailUserUpdate(domain_verified=True))
        
        return {
            "domain_verified": is_valid,
            "validation_details": domain_info.get("validation_results", {})
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during domain verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in domain verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error verifying domain: {str(e)}"
        )

@router.post("/send")
async def send_email_with_storage(data: SendEmailRequest, current_user_id: str = Depends(get_current_user_id)):
    try:
        user = await get_email_user(current_user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email user not found"
            )
        
        if not user.get("api_key"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No API key configured for this user"
            )
        
        if not user.get("domain_verified"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Domain not verified. Please complete domain verification first."
            )
        content_type = getattr(data, "content_type", "text/html")
        if content_type == "text/plain":
            message = Mail(
            from_email=data.from_email,
            to_emails=data.to,
            subject=data.subject,
            plain_text_content=data.content
            )
        else:
            # default to html
            message = Mail(
            from_email=data.from_email,
            to_emails=data.to,
            subject=data.subject,
            html_content=data.content
            )
        sg = SendGridAPIClient(user["api_key"])
        response = sg.send(message)
        
        await log_email_send(
            user_id=current_user_id,
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
    except HTTPException:
        raise
    except Exception as e:
        await log_email_send(
            user_id=current_user_id,
            to_email=data.to,
            from_email=data.from_email,
            subject=data.subject,
            status="failed"
        )
        logger.error(f"Error sending email: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error sending email: {str(e)}"
        )

@router.get("/logs")
async def get_email_logs_endpoint(current_user_id: str = Depends(get_current_user_id), limit: int = 50):
    try:
        email_logs_collection = await get_email_logs_collection()
        cursor = email_logs_collection.find({"user_id": current_user_id}).sort("timestamp", -1).limit(limit)
        logs = await cursor.to_list(length=limit)
        return {"user_id": current_user_id, "logs": logs, "total": len(logs)}
    except Exception as e:
        logger.error(f"Error retrieving email logs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving email logs: {str(e)}"
        )

# Direct endpoints for manual operations (admin use) - These remain unchanged as they're for admin use
@router.post("/create_subuser", status_code=status.HTTP_201_CREATED)
def create_subuser(data: SubuserCreate):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
        
    try:
        payload = data.dict()
        headers = {
            "Authorization": f"Bearer {SENDGRID_MASTER_KEY}",
            "Content-Type": "application/json"
        }
        resp = requests.post(f"{SG_BASE}/subusers", json=payload, headers=headers)
        if resp.status_code != 201:
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"Manual subuser creation failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid API error: {error_detail}"
                )
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during manual subuser creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in manual subuser creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/add_domain", status_code=status.HTTP_201_CREATED)
def add_domain(data: DomainCreate):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
        
    try:
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
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"Manual domain setup failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid domain setup error: {error_detail}"
                )
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during manual domain setup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in manual domain setup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/create_subuser_apikey/{username}", status_code=status.HTTP_201_CREATED)
def create_subuser_apikey(username: str, key_name: str = None):
    if not SENDGRID_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SendGrid master key not configured"
        )
        
    try:
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
            error_detail = resp.json() if resp.text else "No response body"
            logger.error(f"Manual API key creation failed - Status: {resp.status_code}, Error: {error_detail}")
            
            if resp.status_code == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request to SendGrid: {error_detail}"
                )
            elif resp.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SendGrid authentication failed"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SendGrid API key creation error: {error_detail}"
                )
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during manual API key creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in manual API key creation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/webhook")
async def sendgrid_webhook(request: Request):
    try:
        events = await request.json()
        for event in events:
            email = event.get("email")
            event_type = event.get("event")
            logger.info(f"Email event: {email}, {event_type}")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing SendGrid webhook: {str(e)}")
        return Response(status_code=status.HTTP_200_OK)  