from fastapi import Depends, HTTPException, status, Header
import jwt
from config import SECRET_KEY, ALGORITHM
from services.database import get_users_collection
from utils.security import verify_password
from services.api_key_service import APIKeyService
from services.database import get_users_collection
from bson import ObjectId
from fastapi import HTTPException, status, Header
import logging

logger = logging.getLogger(__name__)

async def validate_api_key(required_scope: str, x_api_key: str = Header(None)):
    """
    Validate API key for specific scope
    Can be used as a dependency in routes
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate API key",
        headers={"WWW-Authenticate": "APIKey"},
    )
    
    if not x_api_key:
        logger.error("Missing X-API-Key header")
        raise credentials_exception
    
    # Validate the API key
    validation_result = APIKeyService.validate_api_key(x_api_key, required_scope)
    if not validation_result["valid"]:
        logger.error(f"API key validation failed: {validation_result['error']}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=validation_result["error"]
        )
    
    # Get user from database
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"_id": ObjectId(validation_result["user_id"])})
    
    if user is None:
        logger.error(f"User not found for API key: {validation_result['user_id']}")
        raise credentials_exception
        
    return user

async def get_current_user(authorization: str = Header(None)):
    """
    Get current user from JWT token
    Accepts both 'Authorization' and 'authorization' header names
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )
    
    # Check if authorization header exists
    if not authorization:
        logger.error("Missing Authorization header")
        raise credentials_exception
    
    try:
        # Extract token from "Bearer <token>" format
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.error(f"Invalid Authorization header format: {authorization}")
            raise credentials_exception
            
        token = parts[1]
        
        # Decode JWT token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        
        if email is None:
            logger.error("No email in token payload")
            raise credentials_exception
            
    except jwt.ExpiredSignatureError:
        logger.error("JWT token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid JWT token: {e}")
        raise credentials_exception
    except Exception as e:
        logger.error(f"Unexpected error in token validation: {e}")
        raise credentials_exception
    
    # Get user from database
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"email": email})
    
    if user is None:
        logger.error(f"User not found for email: {email}")
        raise credentials_exception
        
    return user

async def authenticate_user(email: str, password: str):
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"email": email})
    if not user or not verify_password(password, user.get('hashed_password', '')):
        return False
    return user