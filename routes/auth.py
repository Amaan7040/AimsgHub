from fastapi import APIRouter, HTTPException, status, Header
from models.users import UserCreate, UserResponse, UserLogin, Token
from services.database import get_users_collection
from services.auth import get_current_user, authenticate_user
from utils.security import get_password_hash, create_access_token
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
    
    access_token = create_access_token(data={"sub": user['email']})
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "user_id": str(user["_id"]),
        "email": user["email"]
    }