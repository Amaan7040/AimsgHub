from pydantic import BaseModel, EmailStr, validator, Field
import re
from typing import Optional
from datetime import datetime
from .base import BaseMongoModel, PyObjectId

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    username: str
    mobile_number: str

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

    @validator('username')
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Username can only contain letters, numbers and underscores')
        return v

    @validator('mobile_number')
    def validate_mobile_number(cls, v):
        cleaned = re.sub(r'[\s\-\(\)\.\+]', '', v)
        
        if not cleaned.isdigit():
            raise ValueError('Mobile number must contain only digits (plus optional spaces, dashes, parentheses, dots, or +)')
        
        if len(cleaned) < 7:
            raise ValueError('Mobile number is too short (minimum 7 digits)')
        if len(cleaned) > 15:
            raise ValueError('Mobile number is too long (maximum 15 digits)')
        
        return v  


class UserResponse(BaseMongoModel):
    id: str = Field(alias='_id')
    email: EmailStr
    username: str
    mobile_number: str
    whatsapp_account_verified: bool
    chatbot_active: bool
    created_at: datetime

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None
