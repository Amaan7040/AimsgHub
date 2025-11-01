import logging
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from services.api_key_service import APIKeyService
from services.database import get_api_keys_collection

logger = logging.getLogger(__name__)
security = HTTPBearer()

class DeviceAuth:
    """Authentication and authorization for device operations"""
    
    @staticmethod
    async def validate_device_access(credentials: HTTPAuthorizationCredentials, required_scope: str):
        """Validate API key for device operations"""
        try:
            api_key = credentials.credentials
            
            # First try API key validation
            validation_result = APIKeyService.validate_api_key(api_key, required_scope)
            
            if validation_result["valid"]:
                return {
                    "user_id": validation_result["user_id"],
                    "scope": validation_result["scope"],
                    "auth_type": "api_key"
                }
            
            # If API key fails, check if it's a JWT token (for frontend)
            from services.auth import verify_token
            try:
                user_data = verify_token(api_key)
                return {
                    "user_id": user_data["_id"],
                    "scope": "full_access",  # JWT has full access
                    "auth_type": "jwt"
                }
            except:
                pass
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid API key or token for scope: {required_scope}"
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Device auth error: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )

# Dependency functions for different device operations
async def require_device_create(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_create")

async def require_device_read(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_read")

async def require_device_update(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_update")

async def require_device_delete(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_delete")

async def require_device_qr_read(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_qr_read")

async def require_device_qr_refresh(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_qr_refresh")

async def require_device_status_read(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "device_status_read")

async def require_whatsapp_send(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "whatsapp_device_send")

async def require_whatsapp_receive(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await DeviceAuth.validate_device_access(credentials, "whatsapp_device_receive")