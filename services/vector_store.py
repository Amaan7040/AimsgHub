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
from utils.file_processing import load_documents, split_documents
from services.database import get_users_collection
from config import VECTOR_STORE_DIR, BATCH_SIZE

logger = logging.getLogger(__name__)

def create_vector_store(documents: List[Document], embedding_function):
    """Create a new vector store from documents"""
    try:
        # Create a temporary directory for the vector store
        vector_store_path = f"temp_vector_store_{uuid.uuid4().hex}"
        os.makedirs(vector_store_path, exist_ok=True)
        
        # Create Chroma vector store
        vector_store = Chroma.from_documents(
            documents=documents,
            embedding=embedding_function,
            persist_directory=vector_store_path
        )
        
        # Persist the vector store
        vector_store.persist()
        
        return vector_store
        
    except Exception as e:
        logger.error(f"Error creating vector store: {e}")
        raise

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
    user_pattern = f"user_{user_id}"
    
    try:
        # Look for user directories in vector_stores folder
        vector_stores_base = "vector_stores"
        if os.path.exists(vector_stores_base):
            for item in os.listdir(vector_stores_base):
                item_path = os.path.join(vector_stores_base, item)
                
                if exclude_current_path and item_path == exclude_current_path:
                    continue
                    
                if item.startswith(user_pattern):
                    logger.info(f"Found old vector store to delete: {item_path}")
                    await cleanup_vector_store_resources(item_path)
    except Exception as e:
        logger.error(f"Error during force cleanup of old vector stores: {e}")

async def create_or_update_vector_store(user: dict, file):
    """Create or update vector store from file - simplified version for knowledge base"""
    temp_file_path = None
    vector_store = None
    
    try:
        from utils.file_processing import get_file_type, load_documents, split_documents
        
        # Get file info
        filename = file.filename
        file_type = get_file_type(filename)
        
        # Create user directory
        user_dir = f"vector_stores/user_{user['_id']}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Save file temporarily
        temp_file_path = f"{user_dir}/temp_{uuid.uuid4().hex}_{filename}"
        with open(temp_file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Check file size (10MB max)
        if len(content) > 10 * 1024 * 1024:
            os.remove(temp_file_path)
            raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")
        
        # Load and process documents
        documents = load_documents(temp_file_path, file_type)
        split_docs = split_documents(documents)
        
        if not split_docs:
            raise HTTPException(status_code=400, detail="No content could be extracted from the file")
        
        logger.info(f"Processed {filename}: {len(split_docs)} chunks")
        
        # Create NEW vector store
        vector_store = create_vector_store(split_docs, embedding_model)
        
        # Save vector store to user directory
        vector_store_path = f"{user_dir}/vector_store"
        vector_store.save_local(vector_store_path)
        
        # Clean up old vector store if exists
        old_vector_store_path = user.get('vector_store_path')
        if old_vector_store_path and os.path.exists(old_vector_store_path):
            logger.info(f"Cleaning up old vector store: {old_vector_store_path}")
            await cleanup_vector_store_resources(old_vector_store_path)
        
        return vector_store_path, len(split_docs)
        
    except Exception as e:
        logger.error(f"Error creating/updating vector store: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing knowledge base: {str(e)}")
    finally:
        # Clean up temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"Could not delete temporary file {temp_file_path}: {e}")
        
        # Clean up vector store resources
        if vector_store:
            close_vector_store(vector_store)
        
        gc.collect()