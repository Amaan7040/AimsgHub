import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import HTTPException
from config import REFRESH_TOKEN_EXPIRE_DAYS, ACCESS_TOKEN_EXPIRE_MINUTES
from utils.security import create_access_token, create_refresh_token, verify_token
from services.database import get_refresh_tokens_collection

logger = logging.getLogger(__name__)

class TokenService:
    
    @staticmethod
    async def create_tokens_for_user(user: dict):
        """Create both access and refresh tokens for a user"""
        try:
            # Create access token
            access_token = create_access_token(data={"sub": user["email"]})
            
            # Create refresh token
            refresh_token = create_refresh_token()
            
            # Store refresh token in database
            refresh_tokens_collection = await get_refresh_tokens_collection()
            
            refresh_token_doc = {
                "user_id": ObjectId(user["_id"]),
                "refresh_token": refresh_token,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
                "is_revoked": False
            }
            
            await refresh_tokens_collection.insert_one(refresh_token_doc)
            
            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60  # in seconds
            }
            
        except Exception as e:
            logger.error(f"Error creating tokens: {e}")
            raise
    
    @staticmethod
    async def refresh_access_token(refresh_token: str):
        """Refresh access token using valid refresh token"""
        try:
            refresh_tokens_collection = await get_refresh_tokens_collection()
            
            # Find valid refresh token
            token_doc = await refresh_tokens_collection.find_one({
                "refresh_token": refresh_token,
                "is_revoked": False,
                "expires_at": {"$gt": datetime.now(timezone.utc)}
            })
            
            if not token_doc:
                raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
            
            # Get user data
            users_collection = await get_users_collection()
            user = await users_collection.find_one({"_id": token_doc["user_id"]})
            
            if not user:
                raise HTTPException(status_code=401, detail="User not found")
            
            # Create new access token
            new_access_token = create_access_token(data={"sub": user["email"]})
            
            return {
                "access_token": new_access_token,
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")
    
    @staticmethod
    async def revoke_refresh_token(refresh_token: str):
        """Revoke a refresh token (logout)"""
        try:
            refresh_tokens_collection = await get_refresh_tokens_collection()
            
            await refresh_tokens_collection.update_one(
                {"refresh_token": refresh_token},
                {"$set": {"is_revoked": True}}
            )
            
        except Exception as e:
            logger.error(f"Error revoking token: {e}")
    
    @staticmethod
    async def revoke_all_user_tokens(user_id: str):
        """Revoke all refresh tokens for a user"""
        try:
            refresh_tokens_collection = await get_refresh_tokens_collection()
            
            await refresh_tokens_collection.update_many(
                {"user_id": ObjectId(user_id)},
                {"$set": {"is_revoked": True}}
            )
            
        except Exception as e:
            logger.error(f"Error revoking user tokens: {e}")

# Helper function to get users collection
async def get_users_collection():
    from services.database import get_users_collection
    return await get_users_collection()