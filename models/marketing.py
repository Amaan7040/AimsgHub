from pydantic import BaseModel, EmailStr, validator
import re
from typing import Optional, List
from datetime import datetime

# SMS Marketing Models
class NumberRequest(BaseModel):
    user_id: str
    phone_number: str

class OTPVerifyRequest(BaseModel):
    user_id: str
    code: str

class SMSRequest(BaseModel):
    user_id: str
    to_number: str
    message: str

# Email Marketing Models
class EmailUserCreate(BaseModel):
    user_id: str
    username: str
    email: EmailStr

class EmailUserUpdate(BaseModel):
    api_key: Optional[str] = None
    domain: Optional[str] = None
    subdomain: Optional[str] = None
    domain_id: Optional[str] = None
    domain_verified: Optional[bool] = None
    subuser_username: Optional[str] = None
    subuser_id: Optional[str] = None

class SendEmailRequest(BaseModel):
    user_id: str
    to: EmailStr
    from_email: EmailStr
    subject: str
    content: str

class SubuserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class DomainCreate(BaseModel):
    domain: str
    subdomain: str
    username: str

class SendEmailModel(BaseModel):
    to: EmailStr
    from_email: EmailStr
    subject: str
    content: str
    api_key: str
