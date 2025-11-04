from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from services.auth import get_current_user
from services.api_key_service import APIKeyService
from services.database import get_api_keys_collection
from config import API_KEY_EXPIRY_HOURS
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["API Keys"])

@router.post("/generate")
async def generate_api_keys(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Generate full set of API keys for a user"""
    try:
        api_keys_collection = await get_api_keys_collection()
        user_id = str(current_user["_id"])
        
        generated_keys = await APIKeyService.generate_all_keys_for_user(user_id, api_keys_collection)
        
        logger.info(f"Generated API keys for user: {current_user['email']}")
        
        return {
            "success": True,
            "keys": generated_keys,
            "message": f"API keys generated successfully. They will expire in {API_KEY_EXPIRY_HOURS} hours."
        }
        
    except Exception as e:
        logger.error(f"Error generating API keys: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to generate API keys"
        )

@router.get("/my-keys")
async def get_my_keys(current_user: dict = Depends(get_current_user)):
    """Get user's current API keys with auto-rotation"""
    try:
        api_keys_collection = await get_api_keys_collection()
        # Use the auto-rotation method
        user_keys = await APIKeyService.get_user_keys_with_auto_rotate(str(current_user["_id"]), api_keys_collection)
        
        return {
            "success": True,
            "keys": user_keys,
            "user_email": current_user["email"],
            "auto_rotation_enabled": True
        }
        
    except Exception as e:
        logger.error(f"Error getting user keys: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to retrieve API keys"
        )
    
@router.post("/rotate")
async def rotate_keys(current_user: dict = Depends(get_current_user)):
    """Manually rotate API keys"""
    try:
        api_keys_collection = await get_api_keys_collection()
        user_id = str(current_user["_id"])
        
        # Force generate new keys
        generated_keys = await APIKeyService.generate_all_keys_for_user(user_id, api_keys_collection)
        
        logger.info(f"Rotated API keys for user: {current_user['email']}")
        
        return {
            "success": True,
            "keys": generated_keys,
            "message": "API keys rotated successfully."
        }
        
    except Exception as e:
        logger.error(f"Error rotating API keys: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to rotate API keys"
        )