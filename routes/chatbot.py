from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Optional
from models.campaigns import ChatTestInput, KnowledgeBaseInput
from services.database import get_users_collection
from services.auth import get_current_user
from services.vector_store import create_or_update_vector_store, load_vector_store_safely, close_vector_store, create_advanced_retriever, force_delete_old_vector_stores
from utils.embeddings import embedding_model
from utils.file_processing import valid_url
from config import GROQ_API_KEY
import logging
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chatbot", tags=["Chatbot"])

@router.post("/knowledge-base", status_code=200)
async def upload_knowledge_base(
    knowledge_file: Optional[UploadFile] = File(None),
    knowledge_url: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    if not knowledge_file and not knowledge_url:
        raise HTTPException(status_code=400, detail="Either file or URL must be provided")
    
    if knowledge_file and knowledge_url:
        raise HTTPException(status_code=400, detail="Provide either file or URL, not both")
    
    try:
        if knowledge_file:
            if not knowledge_file.filename:
                raise HTTPException(status_code=400, detail="No file provided")
            
            allowed_types = ['.txt', '.pdf', '.docx', '.csv']
            if not any(knowledge_file.filename.lower().endswith(ext) for ext in allowed_types):
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid file type. Supported types: {', '.join(allowed_types)}"
                )
            
            filename = knowledge_file.filename.lower()
            if filename.endswith('.txt'):
                file_type = "text"
            elif filename.endswith('.pdf'):
                file_type = "pdf"
            elif filename.endswith('.docx'):
                file_type = "docx"
            elif filename.endswith('.csv'):
                file_type = "csv"
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type")
            
            user_vs_path = await create_or_update_vector_store(current_user, knowledge_file, file_type)
            
        else:
            if not valid_url(knowledge_url):
                raise HTTPException(status_code=400, detail="Invalid URL provided")
            
            user_vs_path = await create_or_update_vector_store(current_user, knowledge_url, "website")
        
        if user_vs_path:
            return {"message": "Chatbot knowledge base updated successfully with advanced processing."}
        else:
            raise HTTPException(status_code=400, detail="Failed to process knowledge base.")
            
    except Exception as e:
        logger.error(f"Error processing knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing knowledge base: {str(e)}")

@router.post("/activate")
async def activate_chatbot(current_user: dict = Depends(get_current_user)):
    users_collection = await get_users_collection()
    await users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"chatbot_active": True}}
    )
    return {"message": "Chatbot activated successfully.", "status": True}

@router.post("/deactivate")
async def deactivate_chatbot(current_user: dict = Depends(get_current_user)):
    users_collection = await get_users_collection()
    await users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"chatbot_active": False}}
    )
    return {"message": "Chatbot deactivated successfully.", "status": False}

@router.get("/status")
async def get_chatbot_status(current_user: dict = Depends(get_current_user)):
    return {
        "chatbot_active": current_user.get("chatbot_active", False),
        "email": current_user["email"]
    }

@router.post("/test-query")
async def test_chatbot_query(
    data: ChatTestInput,
    current_user: dict = Depends(get_current_user)
):
    if not current_user.get('vector_store_path'):
        raise HTTPException(status_code=400, detail="No knowledge base available. Please upload documents or a website first.")
    
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

Note: Do not tell from  ur side that i do not find from theknowledge base provided instead just say an appology message that i do not have that information to that.
Do not use any vague or introduction message. Also if there is no context then just say that "Sorry, I donot have any information regarding this."

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
    if not current_user.get('vector_store_path'):
        return {"status": "no_knowledge_base", "message": "No knowledge base configured"}
    
    vector_store = None
    try:
        loop = asyncio.get_event_loop()
        vector_store = await loop.run_in_executor(
            None, load_vector_store_safely, current_user['vector_store_path']
        )
        
        sample_docs = vector_store.similarity_search("", k=3)
        
        return {
            "status": "active",
            "vector_store_path": current_user['vector_store_path'],
            "sample_documents": [
                {
                    "content_preview": doc.page_content[:100] + "..." if len(doc.page_content) > 100 else doc.page_content,
                    "source": doc.metadata.get('source', 'unknown')
                }
                for doc in sample_docs
            ],
            "total_documents": vector_store._collection.count()
        }
    except Exception as e:
        return {"status": "error", "message": f"Error loading knowledge base: {str(e)}"}
    finally:
        if vector_store:
            close_vector_store(vector_store)

@router.post("/cleanup-old-knowledge-bases")
async def cleanup_old_knowledge_bases(current_user: dict = Depends(get_current_user)):
    try:
        await force_delete_old_vector_stores(str(current_user['_id']), current_user.get('vector_store_path'))
        return {"message": "Cleanup of old knowledge bases completed"}
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise HTTPException(status_code=500, detail=f"Error during cleanup: {str(e)}")