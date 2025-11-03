from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from bson import ObjectId
from .base import PyObjectId

class DeviceBase(BaseModel):
    name: str
    login_type: str = Field(..., pattern="^(QR Login|Phone Login)$")
    instance_id: Optional[str] = None
    phone_number: Optional[str] = None
    status: Optional[str] = Field(default="inactive")
    webhook_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @validator('status')
    def validate_status(cls, v):
        if v and v not in ["active", "inactive", "pending", "error"]:
            raise ValueError('Status must be one of: active, inactive, pending, error')
        return v

class DeviceCreate(DeviceBase):
    # Remove user_id from create since it comes from auth
    pass

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    login_type: Optional[str] = Field(None, pattern="^(QR Login|Phone Login)$")
    status: Optional[str] = None
    webhook_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    phone_number: Optional[str] = None
    instance_id: Optional[str] = None

    @validator('status')
    def validate_status(cls, v):
        if v and v not in ["active", "inactive", "pending", "error"]:
            raise ValueError('Status must be one of: active, inactive, pending, error')
        return v

class DeviceResponse(DeviceBase):
    id: PyObjectId = Field(alias="_id")
    user_id: PyObjectId
    qr_code: Optional[str] = None
    qr_code_generated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class DeviceQRResponse(BaseModel):
    qr_code: str
    qr_code_generated_at: datetime
    expires_in: int = 300

class DeviceStatusResponse(BaseModel):
    status: str
    is_connected: bool
    last_seen: Optional[datetime] = None
    connection_info: Optional[Dict[str, Any]] = None