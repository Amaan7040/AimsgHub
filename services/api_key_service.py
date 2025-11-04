import secrets
import logging
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import HTTPException, status
from config import API_KEY_EXPIRY_HOURS, API_KEY_SCOPES

logger = logging.getLogger(__name__)

class APIKeyService:
    
    @staticmethod
    def _ensure_timezone_aware(dt):
        """Ensure datetime is timezone-aware (UTC)"""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    
    @staticmethod
    def _get_current_time():
        """Get current time in UTC timezone-aware format"""
        return datetime.now(timezone.utc)

    @staticmethod
    def generate_scoped_key(user_id: str, scope: str) -> dict:
        """Generate a scoped API key for a user"""
        try:
            current_time = APIKeyService._get_current_time()
            timestamp = int(current_time.timestamp())
            
            # Replace underscores in scope with hyphens to avoid splitting issues
            scope_safe = scope.replace('_', '-')
            
            key_string = f"user_{user_id}_{scope_safe}_{timestamp}"
            secret_part = secrets.token_urlsafe(32)
            full_key = f"{key_string}_{secret_part}"
            
            return {
                "key": full_key,
                "expires_at": current_time + timedelta(hours=API_KEY_EXPIRY_HOURS),
                "scope": scope,
                "generated_at": current_time
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
            current_time = APIKeyService._get_current_time()
            if current_time - key_time > timedelta(hours=API_KEY_EXPIRY_HOURS):
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
    async def auto_rotate_if_needed(user_id: str, db_collection):
        """Auto-rotate API keys if they're about to expire"""
        try:
            user_keys = await db_collection.find_one({"user_id": ObjectId(user_id)})
            
            if not user_keys:
                return await APIKeyService.generate_all_keys_for_user(user_id, db_collection)
            
            # Check if any key needs rotation (within 30 mins of expiry)
            needs_rotation = False
            current_time = APIKeyService._get_current_time()
            
            for scope, key_data in user_keys.get("keys", {}).items():
                expires_at = key_data.get("expires_at")
                if expires_at:
                    # Ensure both datetimes are timezone-aware for comparison
                    expires_at = APIKeyService._ensure_timezone_aware(expires_at)
                    
                    time_until_expiry = expires_at - current_time
                    if time_until_expiry < timedelta(hours=0.5):  # 30 minutes
                        needs_rotation = True
                        break
            
            if needs_rotation:
                logger.info(f"Auto-rotating API keys for user {user_id}")
                return await APIKeyService.generate_all_keys_for_user(user_id, db_collection)
            
            return user_keys.get("keys", {})
            
        except Exception as e:
            logger.error(f"Error in auto-rotation: {e}")
            return {}
       
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
            current_time = APIKeyService._get_current_time()
            await db_collection.update_one(
                {"user_id": ObjectId(user_id)},
                {
                    "$set": {
                        "keys": generated_keys, 
                        "last_rotated": current_time,
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
            current_time = APIKeyService._get_current_time()
            
            if last_rotated:
                # Ensure both datetimes are timezone-aware
                last_rotated = APIKeyService._ensure_timezone_aware(last_rotated)
                
                if current_time - last_rotated > timedelta(hours=API_KEY_EXPIRY_HOURS):
                    # Auto-rotate expired keys
                    return await APIKeyService.generate_all_keys_for_user(user_id, db_collection)
            
            # Convert datetime objects to strings for response
            response_keys = {}
            for scope, key_data in user_keys.get("keys", {}).items():
                expires_at = key_data.get("expires_at")
                if expires_at:
                    expires_at = APIKeyService._ensure_timezone_aware(expires_at)
                    if expires_at > current_time:
                        response_keys[scope] = {
                            "key": key_data["key"],
                            "expires_at": key_data["expires_at"].isoformat(),
                            "generated_at": key_data.get("generated_at", current_time).isoformat()
                        }
            
            return response_keys
            
        except Exception as e:
            logger.error(f"Error getting keys for user {user_id}: {e}")
            return {}

    @staticmethod
    async def get_user_keys_with_auto_rotate(user_id: str, db_collection):
        """Get user keys with automatic rotation if needed"""
        try:
            # Auto-rotate if needed
            keys = await APIKeyService.auto_rotate_if_needed(user_id, db_collection)
            
            if not keys:
                # Generate new keys if none exist
                keys = await APIKeyService.generate_all_keys_for_user(user_id, db_collection)
            
            # Convert datetime objects to strings for response
            current_time = APIKeyService._get_current_time()
            response_keys = {}
            for scope, key_data in keys.items():
                expires_at = key_data.get("expires_at")
                if expires_at:
                    expires_at = APIKeyService._ensure_timezone_aware(expires_at)
                    if expires_at > current_time:
                        response_keys[scope] = {
                            "key": key_data["key"],
                            "expires_at": key_data["expires_at"].isoformat(),
                            "generated_at": key_data.get("generated_at", current_time).isoformat(),
                            "auto_rotated": True  # Flag to indicate auto-rotation
                        }
            
            return response_keys
            
        except Exception as e:
            logger.error(f"Error getting keys with auto-rotate for user {user_id}: {e}")
            return {}