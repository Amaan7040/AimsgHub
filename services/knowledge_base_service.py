import os
import shutil
import logging
from bson import ObjectId
from datetime import datetime, timezone

from utils.file_processing import replace_user_knowledge_base

logger = logging.getLogger(__name__)

async def update_replace_user_knowledge_base_service(user_id: str, file, users_collection):
    """Service to replace user's entire knowledge base with new file"""
    
    try:
        # Get user's current vector store path to clean up old files
        user = await users_collection.find_one({"_id": ObjectId(user_id)})
        old_vector_store_path = user.get('vector_store_path') if user else None
        
        # Process the new file (replaces everything)
        vector_store_path, doc_count, filename = await replace_user_knowledge_base(user_id, file)
        
        # Clean up old vector store files
        if old_vector_store_path and os.path.exists(old_vector_store_path):
            try:
                shutil.rmtree(os.path.dirname(old_vector_store_path))
                logger.info(f"Cleaned up old vector store for user {user_id}")
            except Exception as e:
                logger.warning(f"Could not clean up old vector store: {e}")
        
        # Update user in database
        await users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "vector_store_path": vector_store_path,
                "knowledge_base_file": filename,
                "knowledge_base_updated": datetime.now(timezone.utc),
                "documents_count": doc_count
            }}
        )
        
        return {
            "success": True, 
            "documents_processed": doc_count, 
            "filename": filename,
            "message": "Knowledge base replaced successfully"
        }
        
    except Exception as e:
        logger.error(f"Error in knowledge base service: {e}")
        return {
            "success": False,
            "error": str(e)
        }