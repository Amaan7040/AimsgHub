from pydantic import BaseModel, EmailStr, validator
import re
from typing import Optional, List

# SMS Marketing Models
class NumberRequest(BaseModel):
    user_id: str
    phone_number: str

    @validator('phone_number')
    def validate_phone_number(cls, v):
        if not re.match(r'^\+?[1-9]\d{1,14}$', v):
            raise ValueError('Invalid phone number format')
        return v

class OTPVerifyRequest(BaseModel):
    user_id: str
    code: str

class SMSRequest(BaseModel):
    user_id: str
    to_number: str
    message: str

    @validator('to_number')
    def validate_to_number(cls, v):
        if not re.match(r'^\+?[1-9]\d{1,14}$', v):
            raise ValueError('Invalid phone number format')
        return v

# Email Marketing Models
class EmailUserCreate(BaseModel):
    user_id: str
    username: str
    email: str

    @validator('username')
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Username can only contain letters, numbers and underscores')
        return v

class EmailUserUpdate(BaseModel):
    api_key: Optional[str] = None
    domain: Optional[str] = None
    subdomain: Optional[str] = None
    domain_id: Optional[str] = None
    domain_verified: Optional[bool] = None

class SendEmailRequest(BaseModel):
    user_id: str
    to: str
    subject: str
    from_email: str
    content: str

    @validator('to', 'from_email')
    def validate_emails(cls, v):
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Invalid email format')
        return v

class SubuserCreate(BaseModel):
    username: str
    email: str
    password: str = "StrongPassword123!"

class DomainCreate(BaseModel):
    username: str
    domain: str
    subdomain: Optional[str] = "mail"

class SendEmailModel(BaseModel):
    api_key: str
    to: str
    subject: str
    from_email: str
    content: str