from pydantic import BaseModel
from typing import List, Optional

class IdeaInput(BaseModel):
    ai_idea: str

class KnowledgeBaseInput(BaseModel):
    url: Optional[str] = None

class ChatTestInput(BaseModel):
    question: str

class CampaignCreate(BaseModel):
    name: str
    message: Optional[str]
    campaign_type: str
    contacts: Optional[List[str]]