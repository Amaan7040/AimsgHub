from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from bson import ObjectId
from models.base import PyObjectId

class DeviceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    login_type: str = Field(..., regex="^(QR Login|Phone Login)$")
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15)
    status: str = Field(default="active")  # active, inactive, pending
    user_id: PyObjectId

class DeviceCreate(DeviceBase):
    pass

class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    login_type: Optional[str] = Field(None, regex="^(QR Login|Phone Login)$")
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15)
    status: Optional[str] = Field(None, regex="^(active|inactive|pending)$")

class DeviceResponse(DeviceBase):
    id: PyObjectId = Field(alias="_id")
    created_at: datetime
    updated_at: datetime
    qr_data: Optional[str] = None
    instance_id: Optional[str] = None
    
    class Config:
        allow_population_by_field_name = True
        json_encoders = {ObjectId: str}

class DeviceInDB(DeviceBase):
    id: PyObjectId = Field(alias="_id")
    created_at: datetime
    updated_at: datetime
    instance_id: str
    qr_data: Optional[str] = None
    session_data: Optional[dict] = None
    last_connected: Optional[datetime] = None
    
    class Config:
        allow_population_by_field_name = True