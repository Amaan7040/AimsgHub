from fastapi import APIRouter, HTTPException, status, Header, Depends
from models.users import UserCreate, UserResponse, UserLogin
from models.tokens import Token, TokenRefresh  
from services.database import get_users_collection
from services.auth import get_current_user, authenticate_user
from services.token_service import TokenService  
from utils.security import get_password_hash
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user: UserCreate):
    users_collection = await get_users_collection()
    
    existing_user = await users_collection.find_one({
        "$or": [
            {"email": user.email},
            {"username": user.username}
        ]
    })
    if existing_user:
        raise HTTPException(status_code=400, detail="Email or username already registered")
    
    new_user = {
        "email": user.email,
        "username": user.username,
        "mobile_number": user.mobile_number,
        "hashed_password": get_password_hash(user.password),
        "whatsapp_account_verified": False,
        "chatbot_active": True,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await users_collection.insert_one(new_user)
    
    logger.info(f"New user created: {user.email} ({user.username})")
    
    return UserResponse(
        id=str(result.inserted_id),
        username=user.username,
        mobile_number=user.mobile_number,
        email=user.email,
        whatsapp_account_verified=False,
        chatbot_active=True,
        created_at=new_user["created_at"].isoformat()
    )

@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin):
    user = await authenticate_user(user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    # Use TokenService to create both tokens
    tokens = await TokenService.create_tokens_for_user(user)
    
    return {
        "message": "Login successful, Welcome!",
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"], 
        "token_type": tokens["token_type"],
        "user_id": str(user["_id"]),
        "email": user["email"],
        "expires_in": tokens["expires_in"]
    }

@router.post("/refresh", response_model=dict)
async def refresh_token(token_data: TokenRefresh):
    """Refresh access token using refresh token"""
    tokens = await TokenService.refresh_access_token(token_data.refresh_token)
    
    return {
        "message": "Token refreshed successfully",
        "access_token": tokens["access_token"],
        "token_type": tokens["token_type"],
        "expires_in": tokens["expires_in"]
    }

@router.post("/logout")
async def logout(refresh_token: str = None, current_user: dict = Depends(get_current_user)):
    """Logout user and revoke tokens"""
    if refresh_token:
        await TokenService.revoke_refresh_token(refresh_token)
    else:
        # Revoke all user tokens
        await TokenService.revoke_all_user_tokens(str(current_user["_id"]))
    
    return {"message": "Successfully logged out"}