import os
from motor.motor_asyncio import AsyncIOMotorClient # pyright: ignore[reportMissingImports]
from contextlib import asynccontextmanager
import logging
from config import MONGODB_URI, DATABASE_NAME, USERS_COLLECTION, CAMPAIGNS_COLLECTION, MESSAGE_STATUS_COLLECTION, CHAT_HISTORY_COLLECTION, EMAIL_USERS_COLLECTION, EMAIL_LOGS_COLLECTION, SMS_USERS_COLLECTION, SMS_LOGS_COLLECTION, BUSINESS_PROFILES_COLLECTION, TWILIO_NUMBERS_COLLECTION
from fastapi import FastAPI

logger = logging.getLogger(__name__)

class MongoDB:
    client: AsyncIOMotorClient = None
    db = None

mongodb = MongoDB()

async def get_database():
    return mongodb.db

async def get_users_collection():
    db = await get_database()
    return db[USERS_COLLECTION]

async def get_campaigns_collection():
    db = await get_database()
    return db[CAMPAIGNS_COLLECTION]

async def get_message_status_collection():
    db = await get_database()
    return db[MESSAGE_STATUS_COLLECTION]

async def get_chat_history_collection():
    db = await get_database()
    return db[CHAT_HISTORY_COLLECTION]

async def get_email_users_collection():
    db = await get_database()
    return db[EMAIL_USERS_COLLECTION]

async def get_email_logs_collection():
    db = await get_database()
    return db[EMAIL_LOGS_COLLECTION]

async def get_sms_users_collection():
    db = await get_database()
    return db[SMS_USERS_COLLECTION]

async def get_sms_logs_collection():
    db = await get_database()
    return db[SMS_LOGS_COLLECTION]

async def get_business_profiles_collection():
    db = await get_database()
    return db[BUSINESS_PROFILES_COLLECTION]

async def get_twilio_numbers_collection():
    db = await get_database()
    return db[TWILIO_NUMBERS_COLLECTION]

async def get_api_keys_collection():
    from config import API_KEYS_COLLECTION
    db = await get_database()
    return db[API_KEYS_COLLECTION]

async def get_devices_collection():
    db = await get_database()
    return db.devices

async def get_refresh_tokens_collection():
    db = await get_database()
    return db.refresh_tokens

async def get_knowledge_base_collection():
    """Get knowledge base documents collection"""
    from services.database import mongodb
    return mongodb.db.knowledge_base_documents

async def get_devices_collection():
    """Get devices collection for instance mapping"""
    db = await get_database()
    return db.devices

@asynccontextmanager
async def lifespan_manager(app: FastAPI):
    # Startup
    try:
        mongodb.client = AsyncIOMotorClient(MONGODB_URI)
        mongodb.db = mongodb.client[DATABASE_NAME]
        
        await mongodb.client.admin.command('ping')
        logger.info("Successfully connected to MongoDB!")
        
        # Create indexes
        users_collection = await get_users_collection()
        await users_collection.create_index("email", unique=True)
        await users_collection.create_index("created_at")
        
        campaigns_collection = await get_campaigns_collection()
        await campaigns_collection.create_index("owner_id")
        await campaigns_collection.create_index("sent_at")
        
        email_users_collection = await get_email_users_collection()
        await email_users_collection.create_index("user_id", unique=True)
        await email_users_collection.create_index("username", unique=True)
        
        sms_users_collection = await get_sms_users_collection()
        await sms_users_collection.create_index("user_id", unique=True)
        
        # Removed usage collection indexes
        
        logger.info("MongoDB indexes created successfully")
        
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise
    
    yield
    
    # Shutdown
    if mongodb.client:
        mongodb.client.close()
        logger.info("MongoDB connection closed")