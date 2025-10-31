import google.generativeai as genai
import asyncio
import logging
from fastapi import HTTPException
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Configure the SDK at the module level
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not configured on server.")
    # The function will handle raising the HTTP_500
else:
    genai.configure(api_key=GEMINI_API_KEY)

async def call_gemini_api(system_prompt, user_query, model_name: str = "gemini-2.0-flash"):
    """
    Call Gemini API for text generation using the official Google SDK.
    
    Args:
        system_prompt (str): The system instruction for the model.
        user_query (str): The user's prompt.
        model_name (str, optional): The model to use. 
                                    Defaults to "gemini-1.5-flash".
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured on server.")

    try:
        # Initialize the model with the system prompt
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt
        )
        
        # The SDK's generate_content function is blocking, so we run it 
        # in a separate thread to avoid blocking FastAPI's event loop.
        response = await asyncio.to_thread(
            model.generate_content, 
            user_query
        )
        
        # Safely get the text
        if response.text:
            return response.text
        else:
            logger.error("Gemini API returned an empty response.")
            raise HTTPException(status_code=500, detail="AI service returned an empty response.")

    except Exception as e:
        # Catch exceptions from the SDK (e.g., auth errors, model not found)
        logger.error(f"Gemini API call failed: {e}")
        
        # Handle specific known errors from the SDK
        if "API key" in str(e):
             raise HTTPException(status_code=401, detail="Invalid GEMINI_API_KEY.")
        if "404" in str(e) or "not found" in str(e).lower():
             raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found or not available to your API key.")
        
        # General catch-all
        raise HTTPException(status_code=502, detail=f"Error communicating with AI service: {e}")