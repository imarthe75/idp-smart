import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from core.config import settings
from core.idp_logger import logger

# OAuth2 scheme point to Authentik
# In OIDC, the frontend handles the login and provides a Bearer token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.authentik_url}/application/o/authorize/", auto_error=False)

# JWKS Client for Authentik
jwks_url = f"{settings.authentik_internal_url}/application/o/{settings.authentik_client_id}/jwks/"
jwk_client = PyJWKClient(jwks_url)

async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Validates the JWT token from Authentik.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if not token:
        # For development/demo purposes, we might allow a fallback or specific message
        raise credentials_exception

    try:
        # Get the signing key from JWKS
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        
        # Decode and validate
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.authentik_client_id,
            options={"verify_exp": True}
        )
        
        # Extract user info
        user_email = payload.get("email") or payload.get("sub")
        if user_email is None:
            raise credentials_exception
            
        return {
            "email": user_email,
            "username": payload.get("preferred_username") or user_email.split("@")[0],
            "name": payload.get("name", ""),
            "roles": payload.get("groups", []) # Authentik typically puts groups in 'groups' claim
        }
        
    except Exception as e:
        logger.error(f"Error validating Authentik token: {e}")
        raise credentials_exception
