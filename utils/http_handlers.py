"""FastAPI 전역 예외 응답."""

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request


async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """요청 바디/쿼리 검증 실패 시 422와 상세 오류를 돌려준다."""
    return JSONResponse(
        status_code=422,
        content={
            "message": "요청 데이터 검증에 실패했습니다.",
            "detail": exc.errors(),
        },
    )
