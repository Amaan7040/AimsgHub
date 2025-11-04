import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from services.token_service import TokenService
from utils.security import verify_token

logger = logging.getLogger(__name__)

async def token_refresh_middleware(request: Request, call_next):
    """Middleware to automatically refresh tokens when needed"""
    
    # Skip for certain paths
    skip_paths = ['/auth/login', '/auth/signup', '/auth/refresh', '/docs', '/openapi.json']
    if any(request.url.path.startswith(path) for path in skip_paths):
        return await call_next(request)
    
    # Check for Authorization header
    auth_header = request.headers.get('authorization') or request.headers.get('Authorization')
    
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header[7:]
        
        # Verify token
        payload = verify_token(token)
        
        if not payload:
            # Token is invalid or expired
            # Check if we have a refresh token in the request
            refresh_token = request.headers.get('x-refresh-token')
            
            if refresh_token:
                try:
                    # Try to refresh the token
                    new_tokens = await TokenService.refresh_access_token(refresh_token)
                    
                    # Add new access token to response headers
                    response = await call_next(request)
                    response.headers['x-new-access-token'] = new_tokens['access_token']
                    response.headers['x-token-refreshed'] = 'true'
                    
                    return response
                    
                except Exception as e:
                    logger.warning(f"Token refresh failed: {e}")
    
    return await call_next(request)