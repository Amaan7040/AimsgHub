from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from typing import Optional
from models.campaigns import ChatTestInput
from services.database import get_users_collection
from services.auth import get_current_user
from services.vector_store import load_vector_store_safely, close_vector_store, create_advanced_retriever
from utils.embeddings import embedding_model
from config import GROQ_API_KEY
import logging
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
import asyncio
from bson import ObjectId

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chatbot", tags=["Chatbot"])

@router.post("/knowledge-base", status_code=200)
async def upload_knowledge_base(
    knowledge_file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload knowledge base file for chatbot - replaces existing one"""
    try:
        # Validate file type
        allowed_types = ['.txt', '.pdf', '.docx', '.doc']
        file_ext = '.' + knowledge_file.filename.lower().split('.')[-1]
        
        if file_ext not in allowed_types:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type. Supported types: {', '.join(allowed_types)}"
            )
        
        # Import the knowledge base service
        from services.knowledge_base_service import update_replace_user_knowledge_base_service
        
        users_collection = await get_users_collection()
        result = await update_replace_user_knowledge_base_service(current_user["_id"], knowledge_file, users_collection)
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        
        return {
            "message": "Chatbot knowledge base updated successfully",
            "documents_processed": result["documents_processed"],
            "filename": result["filename"]
        }
            
    except Exception as e:
        logger.error(f"Error processing knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing knowledge base: {str(e)}")

@router.post("/activate")
async def activate_chatbot(current_user: dict = Depends(get_current_user)):
    """Activate chatbot functionality"""
    users_collection = await get_users_collection()
    await users_collection.update_one(
        {"_id": ObjectId(current_user["_id"])},
        {"$set": {"chatbot_active": True}}
    )
    return {"message": "Chatbot activated successfully.", "status": True}

@router.post("/deactivate")
async def deactivate_chatbot(current_user: dict = Depends(get_current_user)):
    """Deactivate chatbot functionality"""
    users_collection = await get_users_collection()
    await users_collection.update_one(
        {"_id": ObjectId(current_user["_id"])},
        {"$set": {"chatbot_active": False}}
    )
    return {"message": "Chatbot deactivated successfully.", "status": False}

@router.get("/status")
async def get_chatbot_status(current_user: dict = Depends(get_current_user)):
    """Get current chatbot status"""
    return {
        "chatbot_active": current_user.get("chatbot_active", False),
        "has_knowledge_base": bool(current_user.get('vector_store_path')),
        "knowledge_base_file": current_user.get('knowledge_base_file'),
        "documents_count": current_user.get('documents_count', 0),
        "email": current_user["email"]
    }

@router.post("/test-query")
async def test_chatbot_query(
    data: ChatTestInput,
    current_user: dict = Depends(get_current_user)
):
    """Test chatbot with a query using knowledge base"""
    if not current_user.get('vector_store_path'):
        raise HTTPException(status_code=400, detail="No knowledge base available. Please upload documents first.")
    
    if not current_user.get('chatbot_active', False):
        raise HTTPException(status_code=400, detail="Chatbot is not active. Please activate it first.")
    
    vector_store = None
    try:
        loop = asyncio.get_event_loop()
        vector_store = await loop.run_in_executor(
            None, load_vector_store_safely, current_user['vector_store_path']
        )
        
        advanced_retriever = create_advanced_retriever(vector_store, embedding_model)
        compressed_docs = await loop.run_in_executor(
            None,
            advanced_retriever.get_relevant_documents,
            data.question
        )
        
        docs_text = "\n\n".join([d.page_content for d in compressed_docs[:3]])
        logger.info(f"Retrieved {len(compressed_docs)} documents for test query")

        if not GROQ_API_KEY:
            return {"answer": "AI service is currently unavailable. Please try again later."}

        chat_model = ChatGroq(api_key=GROQ_API_KEY, model="llama-3.3-70b-versatile", temperature=0.3)
        prompt = PromptTemplate.from_template("""
Context: {context}

Question: {question}

Instructions:
- Use the knowledge base content primarily to answer the question
- If the knowledge base doesn't contain relevant information, respond politely that you don't have that information
- Keep responses concise and helpful
- Maintain a friendly, professional tone

Note: Do not tell from your side that you did not find the information in the knowledge base. Instead, just say an apology message that you don't have that information.
Do not use any vague or introduction message. Also if there is no context then just say that "Sorry, I don't have any information regarding this."

Answer:""")
        chain = prompt | chat_model
        response = await chain.ainvoke({"context": docs_text, "question": data.question})
        ai_response = response.content
        
        return {"answer": ai_response}
        
    except Exception as e:
        logger.error(f"Error during test RAG processing: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")
    finally:
        if vector_store:
            close_vector_store(vector_store)

@router.get("/verify-knowledge-base")
async def verify_knowledge_base(current_user: dict = Depends(get_current_user)):
    """Verify knowledge base status and content"""
    if not current_user.get('vector_store_path'):
        return {"status": "no_knowledge_base", "message": "No knowledge base configured"}
    
    vector_store = None
    try:
        loop = asyncio.get_event_loop()
        vector_store = await loop.run_in_executor(
            None, load_vector_store_safely, current_user['vector_store_path']
        )
        
        sample_docs = vector_store.similarity_search("test", k=3)
        
        return {
            "status": "active",
            "vector_store_path": current_user['vector_store_path'],
            "knowledge_base_file": current_user.get('knowledge_base_file'),
            "documents_count": current_user.get('documents_count', 0),
            "sample_documents": [
                {
                    "content_preview": doc.page_content[:100] + "..." if len(doc.page_content) > 100 else doc.page_content,
                    "source": doc.metadata.get('source', 'unknown')
                }
                for doc in sample_docs
            ],
            "total_documents": len(sample_docs)  # Approximate count
        }
    except Exception as e:
        return {"status": "error", "message": f"Error loading knowledge base: {str(e)}"}
    finally:
        if vector_store:
            close_vector_store(vector_store)

@router.delete("/clear-knowledge-base")
async def clear_knowledge_base(current_user: dict = Depends(get_current_user)):
    """Clear user's knowledge base"""
    from services.database import get_users_collection
    import shutil
    import os
    
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"_id": ObjectId(current_user["_id"])})
    
    if user and user.get('vector_store_path'):
        # Delete vector store files
        vector_store_dir = os.path.dirname(user['vector_store_path'])
        if os.path.exists(vector_store_dir):
            shutil.rmtree(vector_store_dir)
    
    # Update user in database
    await users_collection.update_one(
        {"_id": ObjectId(current_user["_id"])},
        {"$unset": {
            "vector_store_path": "",
            "knowledge_base_file": "",
            "knowledge_base_updated": "",
            "documents_count": "",
            "chatbot_active": ""
        }}
    )
    
    return {"success": True, "message": "Knowledge base cleared successfully"}