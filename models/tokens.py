from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user_id: str
    email: str
    expires_in: int

class TokenRefresh(BaseModel):
    refresh_token: str

class TokenData(BaseModel):
    email: Optional[str] = None
    user_id: Optional[str] = None

class RefreshTokenDB(BaseModel):
    user_id: str
    refresh_token: str
    created_at: datetime
    expires_at: datetime
    is_revoked: bool = False