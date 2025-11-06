import os
import logging
import uuid
from typing import List

from langchain_core.documents import Document
from langchain_community.document_loaders import TextLoader, PyMuPDFLoader, Docx2txtLoader
from langchain_community.vectorstores.utils import filter_complex_metadata
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

def get_file_type(filename: str) -> str:
    """Determine file type from filename"""
    ext = filename.lower().split('.')[-1]
    if ext in ['pdf']:
        return 'pdf'
    elif ext in ['docx', 'doc']:
        return 'docx'
    elif ext in ['txt', 'text']:
        return 'text'
    else:
        raise ValueError(f"Unsupported file type: {ext}")

def load_documents(file_path: str, file_type: str):
    """Load documents based on file type (PDF, DOCX, TXT only)"""
    try:
        if file_type == "text":
            loader = TextLoader(file_path, encoding="utf-8")
            documents = loader.load()
        elif file_type == "pdf":
            loader = PyMuPDFLoader(file_path)
            documents = loader.load()
        elif file_type == "docx":
            loader = Docx2txtLoader(file_path)
            documents = loader.load()
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        
        return filter_complex_metadata(documents)
    except Exception as e:
        logger.error(f"Unable to load {file_type} -> {e}")
        raise

def calculate_dynamic_chunk_size(text: str) -> tuple[int, int]:
    """Calculate dynamic chunk size and overlap based on text characteristics"""
    total_chars = len(text)
    dynamic_chunk_size = min(1000, max(200, total_chars // 20))
    dynamic_chunk_overlap = int(dynamic_chunk_size * 0.15)
    return dynamic_chunk_size, dynamic_chunk_overlap

def split_documents(documents: List[Document]) -> List[Document]:
    """Split documents into chunks with dynamic sizing based on content"""
    try:
        if not documents:
            return []
        
        # Combine all document content for analysis
        combined_text = " ".join([doc.page_content for doc in documents])
        
        # Calculate optimal chunk size and overlap dynamically
        chunk_size, chunk_overlap = calculate_dynamic_chunk_size(combined_text)
        
        logger.info(f"Dynamic chunk settings - Size: {chunk_size}, Overlap: {chunk_overlap}")
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )
        
        split_docs = text_splitter.split_documents(documents)
        
        # Log chunk statistics
        if split_docs:
            avg_chunk_length = sum(len(doc.page_content) for doc in split_docs) / len(split_docs)
            logger.info(f"Document splitting complete: {len(split_docs)} chunks, avg length: {avg_chunk_length:.0f} chars")
        
        return split_docs
        
    except Exception as e:
        logger.error(f"Error splitting documents: {e}")
        # Fallback to default splitting
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            length_function=len,
        )
        return text_splitter.split_documents(documents)

async def replace_user_knowledge_base(user_id: str, file):
    """Replace user's entire knowledge base with new single file"""
    try:
        user_dir = f"vector_stores/user_{user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Get file info
        filename = file.filename
        file_type = get_file_type(filename)
        
        # Validate single file
        if not filename:
            raise ValueError("No file provided")
        
        # Save file temporarily
        file_path = f"{user_dir}/temp_{uuid.uuid4()}_{filename}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Check file size (10MB max)
        if len(content) > 10 * 1024 * 1024:
            os.remove(file_path)
            raise ValueError("File size exceeds 10MB limit")
        
        try:
            # Load and process the single file
            documents = load_documents(file_path, file_type)
            split_docs = split_documents(documents)
            
            if not split_docs:
                raise ValueError("No content could be extracted from the file")
            
            logger.info(f"Processed {filename}: {len(split_docs)} chunks with dynamic sizing")
            
            # Import here to avoid circular imports
            from utils.embeddings import embedding_model
            from langchain_community.vectorstores import Chroma
            
            # Create NEW vector store (replaces existing) - FIXED
            vector_store_path = f"{user_dir}/vector_store"
            vector_store = Chroma.from_documents(
                documents=split_docs,
                embedding=embedding_model,
                persist_directory=vector_store_path
            )
            
            logger.info(f"Vector store created successfully at: {vector_store_path}")
            
            return vector_store_path, len(split_docs), filename
            
        finally:
            # Clean up temp file
            if os.path.exists(file_path):
                os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Error replacing knowledge base: {e}")
        raise
    
def get_supported_file_types() -> List[str]:
    """Return list of supported file types"""
    return [".pdf", ".docx", ".doc", ".txt"]