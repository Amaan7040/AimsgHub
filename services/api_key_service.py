import secrets
import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import HTTPException, status
from config import API_KEY_EXPIRY_HOURS, API_KEY_SCOPES

logger = logging.getLogger(__name__)

class APIKeyService:
    @staticmethod
    def generate_scoped_key(user_id: str, scope: str) -> dict:
        """Generate a scoped API key for a user"""
        try:
            timestamp = int(datetime.now(timezone.utc).timestamp())
            
            # Replace underscores in scope with hyphens to avoid splitting issues
            scope_safe = scope.replace('_', '-')
            
            key_string = f"user_{user_id}_{scope_safe}_{timestamp}"
            secret_part = secrets.token_urlsafe(32)
            full_key = f"{key_string}_{secret_part}"
            
            return {
                "key": full_key,
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=API_KEY_EXPIRY_HOURS),
                "scope": scope,
                "generated_at": datetime.now(timezone.utc)
            }
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            raise

    @staticmethod
    def validate_api_key(api_key: str, required_scope: str) -> dict:
        """Validate API key and check permissions"""
        try:
            if not api_key:
                return {"valid": False, "error": "API key required"}
            
            parts = api_key.split('_')
            logger.info(f"API Key parts: {parts}")
            
            if len(parts) < 5:
                return {"valid": False, "error": f"Invalid key format. Expected 5 parts, got {len(parts)}"}
            
            key_type = parts[0]
            user_id = parts[1]
            scope_safe = parts[2]  # Scope with hyphens
            timestamp_str = parts[3]
            
            # Convert scope back to original format
            scope = scope_safe.replace('-', '_')
            
            if key_type != "user":
                return {"valid": False, "error": "Invalid key type"}
            
            # Check if key expired
            try:
                timestamp = int(timestamp_str)
            except ValueError:
                return {"valid": False, "error": f"Invalid timestamp in key: {timestamp_str}"}
                
            key_time = datetime.fromtimestamp(timestamp, timezone.utc)
            if datetime.now(timezone.utc) - key_time > timedelta(hours=API_KEY_EXPIRY_HOURS):
                return {"valid": False, "error": "Key expired"}
            
            # Check scope permission
            if scope != required_scope:
                return {"valid": False, "error": f"Insufficient permissions. Required: {required_scope}, Got: {scope}"}
            
            return {
                "valid": True, 
                "user_id": user_id, 
                "scope": scope,
                "key_timestamp": timestamp
            }
            
        except Exception as e:
            logger.error(f"API key validation error: {e}")
            return {"valid": False, "error": f"Key validation failed: {str(e)}"}
       
    @staticmethod
    async def generate_all_keys_for_user(user_id: str, db_collection):
        """Generate full set of API keys for a user"""
        try:
            generated_keys = {}
            
            for scope in API_KEY_SCOPES:
                key_data = APIKeyService.generate_scoped_key(user_id, scope)
                generated_keys[scope] = {
                    "key": key_data["key"],
                    "expires_at": key_data["expires_at"],
                    "generated_at": key_data["generated_at"]
                }
            
            # Store in database
            await db_collection.update_one(
                {"user_id": ObjectId(user_id)},
                {
                    "$set": {
                        "keys": generated_keys, 
                        "last_rotated": datetime.now(timezone.utc),
                        "user_id": ObjectId(user_id)
                    }
                },
                upsert=True
            )
            
            # Convert datetime objects to strings for response
            response_keys = {}
            for scope, key_data in generated_keys.items():
                response_keys[scope] = {
                    "key": key_data["key"],
                    "expires_at": key_data["expires_at"].isoformat(),
                    "generated_at": key_data["generated_at"].isoformat()
                }
            
            return response_keys
            
        except Exception as e:
            logger.error(f"Error generating all keys for user {user_id}: {e}")
            raise

    @staticmethod
    async def get_user_keys(user_id: str, db_collection):
        """Get user's current API keys"""
        try:
            user_keys = await db_collection.find_one({"user_id": ObjectId(user_id)})
            if not user_keys:
                return {}
            
            # Check if keys need rotation
            last_rotated = user_keys.get("last_rotated")
            if last_rotated and (datetime.now(timezone.utc) - last_rotated > timedelta(hours=API_KEY_EXPIRY_HOURS)):
                # Auto-rotate expired keys
                return await APIKeyService.generate_all_keys_for_user(user_id, db_collection)
            
            # Convert datetime objects to strings for response
            response_keys = {}
            for scope, key_data in user_keys.get("keys", {}).items():
                if isinstance(key_data.get("expires_at"), datetime) and key_data["expires_at"] > datetime.now(timezone.utc):
                    response_keys[scope] = {
                        "key": key_data["key"],
                        "expires_at": key_data["expires_at"].isoformat(),
                        "generated_at": key_data.get("generated_at", datetime.now(timezone.utc)).isoformat()
                    }
            
            return response_keys
            
        except Exception as e:
            logger.error(f"Error getting keys for user {user_id}: {e}")
            return {}