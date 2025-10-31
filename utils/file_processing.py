import os
import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import List
from math import ceil
import logging

from langchain_core.documents import Document
from langchain_community.document_loaders import TextLoader, PyMuPDFLoader, Docx2txtLoader, CSVLoader
from langchain_community.document_loaders import PlaywrightURLLoader
from langchain_community.vectorstores.utils import filter_complex_metadata

logger = logging.getLogger(__name__)

def calculate_dynamic_chunk_size(text: str) -> tuple[int, int]:
    """Calculate dynamic chunk size and overlap based on text characteristics"""
    total_chars = len(text)
    dynamic_chunk_size = min(1000, max(200, total_chars // 20))
    dynamic_chunk_overlap = int(dynamic_chunk_size * 0.15)
    return dynamic_chunk_size, dynamic_chunk_overlap

def valid_url(url: str) -> bool:
    """Validate URL format"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def crawl_links(start_url: str, limit: int = 6) -> List[str]:
    """Crawl website links with limit"""
    visited = set()
    to_visit = [start_url]
    all_links = []

    while to_visit and len(all_links) < limit:
        url = to_visit.pop()
        if url in visited:
            continue
        visited.add(url)
        try:
            r = requests.get(url, timeout=5)
            soup = BeautifulSoup(r.text, "html.parser")
            all_links.append(url)

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith("/"):  # relative link
                    href = start_url.rstrip("/") + href
                if href.startswith(start_url) and href not in visited:
                    to_visit.append(href)
        except Exception as e:
            logger.warning(f"Skipping {url} -> {e}")
    return all_links

def load_documents(file_path: str, file_type: str, url: str = None):
    """Load documents based on file type or URL"""
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
        elif file_type == "csv":
            loader = CSVLoader(file_path)
            documents = loader.load()
        elif file_type == "website":
            if not url:
                raise ValueError("No URL provided for website knowledge base.")
            
            logger.info(f"Crawling website: {url}")
            urls = crawl_links(url, limit=6)
            logger.info(f"Found {len(urls)} pages to load: {urls}")

            try:
                loader = PlaywrightURLLoader(urls=urls, remove_selectors=["header", "nav"])
                documents = loader.load()
                logger.info("Successfully fetched with Playwright")
            except Exception as e:
                logger.warning(f"Playwright failed: {e}, falling back to requests + BeautifulSoup...")
                documents = []
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/114.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                }

                for link in urls:
                    try:
                        r = requests.get(link, headers=headers, timeout=10)
                        soup = BeautifulSoup(r.text, "html.parser")
                        text = soup.get_text(" ", strip=True)

                        if len(text) > 50:
                            documents.append(Document(page_content=text, metadata={"source": link}))
                            logger.info(f"Fetched (fallback) -> {link}")
                    except Exception as e:
                        logger.warning(f"Failed to fetch {link} -> {e}")
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        
        return filter_complex_metadata(documents)
    except Exception as e:
        logger.error(f"Unable to load {file_type} -> {e}")
        raise