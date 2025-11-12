from pydantic import BaseModel, EmailStr, validator
import re
from typing import Optional, List
from datetime import datetime
from typing import Literal
from fastapi import UploadFile

# SMS Marketing Models
class BusinessVerifyRequest(BaseModel):
    business_name: str
    business_type: str
    website: Optional[str] = None
    business_email: Optional[EmailStr] = None

class NumberRequest(BaseModel):
    area_code: Optional[str] = None
    admin_phone: str

    @validator('admin_phone')
    def validate_phone(cls, v):
        if not re.match(r'^\+?[1-9]\d{1,14}$', v):
            raise ValueError('Invalid phone number format')
        return v

class OTPVerifyRequest(BaseModel):
    code: str

class SMSRequest(BaseModel):
    to_number: str
    message: str

    @validator('to_number')
    def validate_phone(cls, v):
        if not re.match(r'^\+?[1-9]\d{1,14}$', v):
            raise ValueError('Invalid phone number format')
        return v

class BulkSMSRequest(BaseModel):
    to_numbers: List[str] = []
    excel_file: Optional[UploadFile] = None
    message: str

    @validator('to_numbers', each_item=True)
    def validate_phones(cls, v):
        if not re.match(r'^\+?[1-9]\d{1,14}$', v):
            raise ValueError(f'Invalid phone number format: {v}')
        return v
    
# Email Marketing Models
class EmailUserCreate(BaseModel):
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
    to: List[EmailStr]
    from_email: EmailStr
    subject: str
    content: str
    content_type: Literal["text/plain", "text/html"] = "text/html" 

class SubuserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class DomainCreate(BaseModel):
    domain: str
    subdomain: str
    username: str

class SendEmailModel(BaseModel):
    to: List[EmailStr]
    from_email: EmailStr
    subject: str
    content: str
    content_type: Literal["text/plain", "text/html"] = "text/html"
    api_key: str