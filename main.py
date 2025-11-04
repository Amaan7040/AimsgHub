import os
import uvicorn
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient # pyright: ignore[reportMissingImports]
from routes.auth import router as auth_router
from routes.sms_marketing import router as sms_router
from routes.email_marketing import router as email_router
from routes.whatsapp import router as whatsapp_router
from routes.campaigns import router as campaigns_router
from routes.chatbot import router as chatbot_router
from routes.analytics import router as analytics_router
from routes.api_keys import router as api_keys_router 
from routes.devices import router as devices_router
from services.token_refresh_middleware import token_refresh_middleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.database import mongodb, get_users_collection, get_campaigns_collection, get_email_users_collection, get_api_keys_collection
    
    # Startup
    try:
        from config import MONGODB_URI, DATABASE_NAME
        mongodb.client = AsyncIOMotorClient(MONGODB_URI)
        mongodb.db = mongodb.client[DATABASE_NAME]
        
        await mongodb.client.admin.command('ping')
        logger.info("Successfully connected to MongoDB!")
        
        users_collection = await get_users_collection()
        await users_collection.create_index("email", unique=True)
        await users_collection.create_index("created_at")

        refresh_tokens_collection = mongodb.db.refresh_tokens
        await refresh_tokens_collection.create_index("user_id")
        await refresh_tokens_collection.create_index("refresh_token", unique=True)
        await refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)  
        await refresh_tokens_collection.create_index([("user_id", 1), ("is_revoked", 1)])
        await refresh_tokens_collection.create_index("created_at")
        logger.info("Refresh tokens collection indexes created")

        devices_collection = mongodb.db.devices
        await devices_collection.create_index("user_id")
        await devices_collection.create_index("instance_id", unique=True)
        await devices_collection.create_index([("user_id", 1), ("name", 1)], unique=True)
        await devices_collection.create_index("created_at")
        await devices_collection.create_index("status")
                
        campaigns_collection = await get_campaigns_collection()
        await campaigns_collection.create_index("owner_id")
        await campaigns_collection.create_index("sent_at")
        
        email_users_collection = await get_email_users_collection()
        await email_users_collection.create_index("user_id", unique=True)
        await email_users_collection.create_index("username", unique=True)
        
        whatsapp_campaigns = mongodb.db.whatsapp_campaigns
        await whatsapp_campaigns.create_index("user_id")
        await whatsapp_campaigns.create_index("created_at")
        await whatsapp_campaigns.create_index([("user_id", 1), ("status", 1)])
        
        whatsapp_auto_replies = mongodb.db.whatsapp_auto_replies
        await whatsapp_auto_replies.create_index("user_id")
        await whatsapp_auto_replies.create_index("keyword")
        await whatsapp_auto_replies.create_index([("user_id", 1), ("is_active", 1)])
        
        whatsapp_templates = mongodb.db.whatsapp_templates
        await whatsapp_templates.create_index("user_id")
        await whatsapp_templates.create_index("created_at")
        
        whatsapp_contacts = mongodb.db.whatsapp_contacts
        await whatsapp_contacts.create_index("user_id")
        await whatsapp_contacts.create_index([("user_id", 1), ("number", 1)], unique=True)
        
        api_keys_collection = await get_api_keys_collection()
        await api_keys_collection.create_index("user_id", unique=True)
        await api_keys_collection.create_index("last_rotated")
        await api_keys_collection.create_index([("user_id", 1), ("last_rotated", -1)])
        
        logger.info("All MongoDB indexes created successfully")
        
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise
    
    yield
    
    # Shutdown
    if mongodb.client:
        mongodb.client.close()
        logger.info("MongoDB connection closed")

app = FastAPI(title="AiMsgHub", version="2.0.0", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],  # NEW: Allow X-API-Key header
)

# Mount static files
app.mount("/static", StaticFiles(directory="."), name="static")

# Include routers
app.include_router(auth_router)
app.include_router(sms_router)
app.include_router(email_router)
app.include_router(whatsapp_router)
app.include_router(campaigns_router)
app.include_router(chatbot_router)
app.include_router(analytics_router)
app.include_router(api_keys_router)  
app.include_router(devices_router)
@app.middleware("http")
async def auto_token_refresh_middleware(request: Request, call_next):
    return await token_refresh_middleware(request, call_next)

@app.get("/")
async def serve_frontend():
    return "Welcome to AiMsgHub! Visit /docs for API Endpoints and docs."

@app.get("/images/{image_name}")
async def serve_image(image_name: str):
    import re
    if not re.match(r'^[a-zA-Z0-9_.-]+$', image_name):
        raise HTTPException(status_code=400, detail="Invalid image name")
    
    image_path = f"./{image_name}"
    if os.path.exists(image_path):
        return FileResponse(image_path)
    else:
        raise HTTPException(status_code=404, detail="Image not found")

@app.get("/health")
async def health_check():
    from services.database import mongodb
    from datetime import datetime, timezone
    try:
        await mongodb.client.admin.command('ping')
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0"
    }

@app.middleware("http")
async def log_headers(request: Request, call_next):
    # Enhanced logging to show API key usage
    headers = dict(request.headers)
    api_key = headers.get('x-api-key') or headers.get('X-API-Key')
    authorization = headers.get('authorization') or headers.get('Authorization')
    
    logger.info(f"Incoming Request: {request.method} {request.url}")
    logger.info(f"Headers: {list(headers.keys())}")
    
    if api_key:
        # Log only first few characters of API key for security
        masked_key = f"{api_key[:10]}..." if len(api_key) > 10 else "***"
        logger.info(f"API Key used: {masked_key}")
    
    if authorization:
        # Log authorization type
        auth_type = authorization.split()[0] if ' ' in authorization else "Unknown"
        logger.info(f"Authorization type: {auth_type}")
    
    response = await call_next(request)
    return response

# NEW: Add API key info endpoint for frontend
@app.get("/api-info")
async def get_api_info():
    """Provide information about API key system"""
    return {
        "api_key_system": "enabled",
        "key_expiry_hours": 3,
        "required_headers": {
            "jwt_auth": "Authorization: Bearer <token>",
            "api_key_auth": "X-API-Key: <scoped_api_key>"
        },
        "available_endpoints": {
            "api_keys": "/api-keys/generate - Generate scoped API keys",
            "api_keys_my": "/api-keys/my-keys - Get current keys",
            "api_keys_rotate": "/api-keys/rotate - Rotate keys manually"
        },
        "scopes": [
            "whatsapp_marketing"
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
