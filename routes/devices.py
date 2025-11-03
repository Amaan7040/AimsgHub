from fastapi import APIRouter, HTTPException, Depends, Header
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone
import logging
from pydantic import ValidationError
import traceback
from services.database import get_devices_collection
from services.auth import validate_api_key
from models.devices import DeviceCreate, DeviceUpdate, DeviceResponse, DeviceQRResponse, DeviceStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["Devices"])

# Dependency for device management API key
async def require_device_management(x_api_key: str = Header(None)):
    return await validate_api_key("device_management", x_api_key)

@router.post("/create", response_model=dict)
async def create_device(
    device_data: DeviceCreate,
    current_user: dict = Depends(require_device_management)
):
    """Create a new device instance"""
    try:
        logger.info(f"Creating device with data: {device_data.dict()}")
        
        devices_collection = await get_devices_collection()
        
        # Check device limit (10 per user)
        user_devices_count = await devices_collection.count_documents({
            "user_id": current_user["_id"]
        })
        
        if user_devices_count >= 10:
            raise HTTPException(
                status_code=400, 
                detail="Device limit reached. Maximum 10 devices per user."
            )
        
        # Check if device name already exists for this user
        existing_device = await devices_collection.find_one({
            "user_id": current_user["_id"],
            "name": device_data.name
        })
        
        if existing_device:
            raise HTTPException(
                status_code=400,
                detail="Device with this name already exists"
            )
        
        # Validate phone number for Phone Login
        if device_data.login_type == "Phone Login":
            if not device_data.phone_number:
                raise HTTPException(
                    status_code=400,
                    detail="Phone number is required for Phone Login"
                )
            if len(device_data.phone_number) != 10 or not device_data.phone_number.isdigit():
                raise HTTPException(
                    status_code=400,
                    detail="Phone number must be 10 digits"
                )
        else:
            # For QR login, ensure phone_number is None
            device_data.phone_number = None
        
        # Create device document
        device_doc = device_data.dict()
        device_doc["user_id"] = ObjectId(current_user["_id"])
        device_doc["created_at"] = datetime.now(timezone.utc)
        device_doc["updated_at"] = datetime.now(timezone.utc)
        
        # Generate instance_id if not provided
        if not device_doc.get("instance_id"):
            device_doc["instance_id"] = f"instance_{str(current_user['_id'])[:10]}_{int(datetime.now().timestamp())}"
        
        logger.info(f"Inserting device: {device_doc}")
        
        result = await devices_collection.insert_one(device_doc)
        
        return {
            "success": True,
            "device_id": str(result.inserted_id),
            "message": "Device created successfully"
        }
        
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")
    
@router.get("/", response_model=List[DeviceResponse])
async def get_devices(
    current_user: dict = Depends(require_device_management)
):
    """Get all devices for the current user"""
    devices_collection = await get_devices_collection()
    
    devices_cursor = devices_collection.find({"user_id": current_user["_id"]})
    devices = await devices_cursor.to_list(length=100)
    
    # Convert to response model
    response_devices = []
    for device in devices:
        device["_id"] = str(device["_id"])
        device["user_id"] = str(device["user_id"])
        response_devices.append(DeviceResponse(**device))
    
    return response_devices

@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    current_user: dict = Depends(require_device_management)
):
    """Get a specific device by ID"""
    devices_collection = await get_devices_collection()
    
    device = await devices_collection.find_one({
        "_id": ObjectId(device_id),
        "user_id": current_user["_id"]
    })
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    device["_id"] = str(device["_id"])
    device["user_id"] = str(device["user_id"])
    
    return DeviceResponse(**device)

@router.put("/update-device/{device_id}", response_model=dict)
async def update_device(
    device_id: str,
    device_data: DeviceUpdate,
    current_user: dict = Depends(require_device_management)
):
    """Update a device"""
    devices_collection = await get_devices_collection()
    
    # Check if device exists and belongs to user
    existing_device = await devices_collection.find_one({
        "_id": ObjectId(device_id),
        "user_id": current_user["_id"]
    })
    
    if not existing_device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Check name uniqueness if name is being updated
    if device_data.name and device_data.name != existing_device.get("name"):
        name_exists = await devices_collection.find_one({
            "user_id": current_user["_id"],
            "name": device_data.name,
            "_id": {"$ne": ObjectId(device_id)}
        })
        
        if name_exists:
            raise HTTPException(
                status_code=400,
                detail="Device with this name already exists"
            )
    
    # Validate phone number for Phone Login
    if (device_data.login_type == "Phone Login" or existing_device.get("login_type") == "Phone Login") and device_data.phone_number:
        if len(device_data.phone_number) != 10 or not device_data.phone_number.isdigit():
            raise HTTPException(
                status_code=400,
                detail="Phone number must be 10 digits"
            )
    
    # Prepare update data
    update_data = device_data.dict(exclude_unset=True)
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    result = await devices_collection.update_one(
        {"_id": ObjectId(device_id), "user_id": current_user["_id"]},
        {"$set": update_data}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=400, detail="No changes made or device not found")
    
    return {
        "success": True,
        "message": "Device updated successfully"
    }

@router.delete("/delete-device/{device_id}", response_model=dict)
async def delete_device(
    device_id: str,
    current_user: dict = Depends(require_device_management)
):
    """Delete a device"""
    devices_collection = await get_devices_collection()
    
    result = await devices_collection.delete_one({
        "_id": ObjectId(device_id),
        "user_id": current_user["_id"]
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Device not found")
    
    return {
        "success": True,
        "message": "Device deleted successfully"
    }

@router.post("/{device_id}/qr", response_model=DeviceQRResponse)
async def generate_qr_code(
    device_id: str,
    current_user: dict = Depends(require_device_management)
):
    """Generate QR code for a device"""
    devices_collection = await get_devices_collection()
    
    # Check if device exists and belongs to user
    device = await devices_collection.find_one({
        "_id": ObjectId(device_id),
        "user_id": current_user["_id"]
    })
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if device.get("login_type") != "QR Login":
        raise HTTPException(
            status_code=400,
            detail="QR code can only be generated for QR Login devices"
        )
    
    # Generate mock QR code data (replace with actual QR generation logic)
    qr_data = f"whatsapp://device/{device.get('instance_id', 'unknown')}/user/{current_user['_id']}"
    
    # Update device with QR info
    await devices_collection.update_one(
        {"_id": ObjectId(device_id)},
        {"$set": {
            "qr_code": qr_data,
            "qr_code_generated_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }}
    )
    
    return DeviceQRResponse(
        qr_code=qr_data,
        qr_code_generated_at=datetime.now(timezone.utc),
        expires_in=300
    )

@router.get("/{device_id}/status", response_model=DeviceStatusResponse)
async def get_device_status(
    device_id: str,
    current_user: dict = Depends(require_device_management)
):
    """Get device connection status"""
    devices_collection = await get_devices_collection()
    
    device = await devices_collection.find_one({
        "_id": ObjectId(device_id),
        "user_id": current_user["_id"]
    })
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Mock status (replace with actual status checking logic)
    is_connected = device.get("status") == "active"
    
    return DeviceStatusResponse(
        status=device.get("status", "inactive"),
        is_connected=is_connected,
        last_seen=device.get("updated_at"),
        connection_info={
            "instance_id": device.get("instance_id"),
            "login_type": device.get("login_type")
        }
    )