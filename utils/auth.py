"""선택적 Bearer 토큰으로 POST /chat 등을 보호한다."""

from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

security = HTTPBearer(auto_error=False)


def require_bearer(
    creds: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> None:
    """CHAT_API_SECRET이 비어 있으면 통과, 있으면 Authorization Bearer와 일치해야 한다."""
    secret = (settings.chat_api_secret or "").strip()
    if not secret:
        return
    if creds is None or creds.credentials != secret:
        raise HTTPException(
            status_code=401,
            detail="인증에 실패했습니다. Authorization Bearer 토큰이 없거나 올바르지 않습니다.",
        )
