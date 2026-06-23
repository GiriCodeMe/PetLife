import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()
_API_KEY = os.getenv("API_KEY", "change-me-in-production")


def require_auth(credentials: HTTPAuthorizationCredentials = Security(_bearer)) -> str:
    if credentials.credentials != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials
