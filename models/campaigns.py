from pydantic import BaseModel
from typing import List, Optional

class IdeaInput(BaseModel):
    idea: str

class KnowledgeBaseInput(BaseModel):
    url: Optional[str] = None

class ChatTestInput(BaseModel):
    question: str

class CampaignCreate(BaseModel):
    name: str
    message: str
    campaign_type: str
    contacts: List[str]