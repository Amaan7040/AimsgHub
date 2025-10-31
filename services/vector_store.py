import os
import time
import re
import uuid
import shutil
import gc
import tempfile
import asyncio
from math import ceil
from typing import List
import logging

from fastapi import HTTPException
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_classic.retrievers import ContextualCompressionRetriever # pyright: ignore[reportMissingImports]
from langchain_classic.retrievers.document_compressors import EmbeddingsFilter # pyright: ignore[reportMissingImports]
from langchain_community.vectorstores.utils import filter_complex_metadata

from utils.embeddings import embedding_model
from utils.file_processing import load_documents, calculate_dynamic_chunk_size, valid_url
from services.database import get_users_collection
from config import VECTOR_STORE_DIR, BATCH_SIZE

logger = logging.getLogger(__name__)

def create_advanced_retriever(vector_store, embedding_model):
    """Create advanced retriever with MMR and contextual compression"""
    mmr_retriever = vector_store.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 10, "lambda_mult": 0.6, "fetch_k": 20}
    )
    
    embeddings_filter = EmbeddingsFilter(
        embeddings=embedding_model,
        similarity_threshold=0.75
    )
    
    return ContextualCompressionRetriever(
        base_compressor=embeddings_filter,
        base_retriever=mmr_retriever
    )

def safe_delete_directory(path: str, max_retries: int = 5, delay: float = 1.0):
    """Safely delete directory with retry logic and proper resource cleanup"""
    if not os.path.exists(path):
        return True
    
    gc.collect()
    
    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            logger.info(f"Successfully deleted directory: {path}")
            return True
        except PermissionError as e:
            logger.warning(f"Permission error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                logger.error(f"Failed to delete {path} after {max_retries} attempts due to permission issues")
                return False
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to delete {path}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                logger.error(f"Failed to delete {path} after {max_retries} attempts")
                return False

async def cleanup_vector_store_resources(vector_store_path: str):
    """Enhanced cleanup with better resource management"""
    if not vector_store_path or not os.path.exists(vector_store_path):
        return
    
    logger.info(f"Starting cleanup of vector store: {vector_store_path}")
    
    try:
        await asyncio.sleep(2)
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, safe_delete_directory, vector_store_path)
        
        if success:
            logger.info(f"Successfully cleaned up vector store: {vector_store_path}")
        else:
            logger.error(f"Failed to clean up vector store: {vector_store_path}")
    except Exception as e:
        logger.error(f"Error during vector store cleanup: {e}")

def load_vector_store_safely(vector_store_path: str):
    """Safely load vector store with proper error handling"""
    if not vector_store_path or not os.path.exists(vector_store_path):
        raise ValueError(f"Vector store path does not exist: {vector_store_path}")
    
    try:
        required_files = ['chroma.sqlite3', 'chroma-collections.parquet', 'chroma-embeddings.parquet']
        existing_files = os.listdir(vector_store_path)
        
        if not any(f in existing_files for f in required_files):
            raise ValueError(f"Vector store at {vector_store_path} appears to be incomplete or corrupted")
        
        vector_store = Chroma(
            persist_directory=vector_store_path,
            embedding_function=embedding_model
        )
        
        test_results = vector_store.similarity_search("test", k=1)
        logger.info(f"Successfully loaded vector store from {vector_store_path} with {len(test_results)} test results")
        
        return vector_store
    except Exception as e:
        logger.error(f"Error loading vector store from {vector_store_path}: {e}")
        raise

def close_vector_store(vector_store):
    """Properly close and cleanup vector store resources"""
    if vector_store is None:
        return
    
    try:
        if hasattr(vector_store, '_client'):
            client = vector_store._client
            if hasattr(client, 'close'):
                client.close()
                logger.info("Closed Chroma client")
        
        del vector_store
        logger.info("Deleted vector store reference")
    except Exception as e:
        logger.error(f"Error closing vector store: {e}")
    finally:
        gc.collect()

async def force_delete_old_vector_stores(user_id: str, exclude_current_path: str = None):
    """Force delete all old vector stores for a user except the current one"""
    user_pattern = f"vs_{user_id}_"
    current_pattern = f"vs_{user_id}$"
    
    try:
        for item in os.listdir(VECTOR_STORE_DIR):
            item_path = os.path.join(VECTOR_STORE_DIR, item)
            
            if exclude_current_path and item_path == exclude_current_path:
                continue
                
            if item.startswith(user_pattern) or re.match(current_pattern, item):
                logger.info(f"Found old vector store to delete: {item_path}")
                await cleanup_vector_store_resources(item_path)
    except Exception as e:
        logger.error(f"Error during force cleanup of old vector stores: {e}")

async def create_or_update_vector_store(user: dict, knowledge_source: str, source_type: str):
    """Create or update vector store with proper resource management"""
    if not knowledge_source:
        return None
    
    temp_file_path = None
    vector_store = None
    
    try:
        timestamp = int(time.time())
        new_vs_path = os.path.join(VECTOR_STORE_DIR, f"vs_{user['_id']}_{timestamp}")
        
        loop = asyncio.get_event_loop()
        if source_type == "website":
            if not valid_url(knowledge_source):
                raise HTTPException(status_code=400, detail="Invalid URL provided")
            
            documents = await loop.run_in_executor(
                None, load_documents, "", "website", knowledge_source
            )
        else:
            if hasattr(knowledge_source, 'filename') and hasattr(knowledge_source, 'read'):
                filename = knowledge_source.filename
                file_extension = os.path.splitext(filename)[1].lower() if filename else '.txt'
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                    content = await knowledge_source.read()
                    temp_file.write(content)
                    temp_file_path = temp_file.name
                
                documents = await loop.run_in_executor(
                    None, load_documents, temp_file_path, source_type
                )
            else:
                raise HTTPException(status_code=400, detail="Invalid file upload")
        
        if not documents:
            raise HTTPException(status_code=400, detail="No content found in the source")
        
        combined_text = "\n".join([doc.page_content for doc in documents])
        chunk_size, chunk_overlap = calculate_dynamic_chunk_size(combined_text)
        
        logger.info(f"Using dynamic chunking - size: {chunk_size}, overlap: {chunk_overlap}")
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len
        )
        
        split_docs = text_splitter.split_documents(documents)
        
        if not split_docs:
            raise HTTPException(status_code=400, detail="No valid documents created after splitting")
        
        logger.info(f"Creating NEW vector store at: {new_vs_path}")
        vector_store = Chroma(
            persist_directory=new_vs_path,
            embedding_function=embedding_model
        )
        
        for i in range(0, len(split_docs), BATCH_SIZE):
            batch = split_docs[i:i + BATCH_SIZE]
            vector_store.add_documents(batch)
            logger.info(f"Embedded batch {i//BATCH_SIZE + 1}/{ceil(len(split_docs)/BATCH_SIZE)}")
        
        vector_store.persist()
        
        old_vector_store_path = user.get('vector_store_path')
        if old_vector_store_path and os.path.exists(old_vector_store_path):
            logger.info(f"Scheduling cleanup of old vector store: {old_vector_store_path}")
            asyncio.create_task(cleanup_vector_store_resources(old_vector_store_path))
            asyncio.create_task(force_delete_old_vector_stores(str(user['_id']), new_vs_path))
        
        # Update user record with NEW path
        users_collection = await get_users_collection()
        await users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"vector_store_path": new_vs_path}}
        )
        
        logger.info(f"Successfully updated vector store. New path: {new_vs_path}")
        return new_vs_path
        
    except Exception as e:
        if vector_store:
            close_vector_store(vector_store)
        logger.error(f"Error processing knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing knowledge base: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.info(f"Cleaned up temporary file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Could not delete temporary file {temp_file_path}: {e}")
        gc.collect()