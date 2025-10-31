import logging
from sendgrid import SendGridAPIClient # pyright: ignore[reportMissingImports]
from sendgrid.helpers.mail import Mail # pyright: ignore[reportMissingImports]
from services.database import get_email_users_collection, get_email_logs_collection
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

async def get_email_user(user_id: str):
    """Get email user from MongoDB"""
    email_users_collection = await get_email_users_collection()
    user = await email_users_collection.find_one({"user_id": user_id})
    return user

async def log_email_send(user_id: str, to_email: str, from_email: str, subject: str, message_id: str = None, status: str = "sent"):
    """Log email sending activity"""
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

async def send_email_with_storage(data):
    """Send email with storage functionality"""
    from models.marketing import SendEmailRequest
    
    if not isinstance(data, SendEmailRequest):
        raise ValueError("Data must be SendEmailRequest instance")
    
    user = await get_email_user(data.user_id)
    if not user:
        raise Exception("Email user not found")
    
    if not user.get("api_key"):
        raise Exception("No API key configured for this user")
    
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
        raise e