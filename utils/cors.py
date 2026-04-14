"""브라우저(Flutter 웹 등)에서 API 호출 시 필요한 CORS 미들웨어 설정."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings


def apply_cors(app: FastAPI) -> None:
    """원격 API 호출 시 CORS 미들웨어를 앱에 붙인다. nginx가 CORS를 담당하면 호출하지 않는다."""
    if settings.cors_allow_all:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return

    extra = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    opts = {
        "allow_origins": extra,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if settings.cors_enable_localhost_regex:
        opts["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    app.add_middleware(CORSMiddleware, **opts)
