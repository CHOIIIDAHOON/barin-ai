from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_APP_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Directory the Cursor agent uses as the project root (must exist on the server).
    cursor_project_dir: str = "/var/cursor-project"
    # Path to `cursor` binary; use full path in systemd (e.g. /usr/local/bin/cursor).
    cursor_cli_path: str = "cursor"
    # Subprocess timeout in seconds (agent runs can be long).
    agent_timeout_sec: int = 600
    # Optional: require this Bearer token on POST /chat (empty = no HTTP auth).
    chat_api_secret: str = ""
    # Cursor CLI --model (run: cursor agent --list-models). Default is Composer fast tier.
    # Override with e.g. auto, gpt-5.4-mini-medium, gemini-3-flash for cost/latency tradeoffs.
    cursor_model: str = "composer-2-fast"
    # Optional: cursor agent --mode. "ask" = read-only Q&A (no edits), usually lower latency than full agent.
    cursor_agent_mode: str = ""

    # Flutter 웹 등: 브라우저 Origin이 API와 다르면 CORS 헤더 필요. 쉼표로 여러 개.
    # 예: CORS_ALLOW_ORIGINS=https://myapp.web.app,http://localhost:5555
    cors_allow_origins: str = ""
    # 로컬 개발용 정규식 (localhost / 127.0.0.1 임의 포트). Ubuntu 배포에서 끄려면 false.
    cors_enable_localhost_regex: bool = True
    # true면 Allow-Origin: * (내부 테스트용). Bearer만 쓰고 쿠키 없으면 보통 동작. 운영은 목록 지정 권장.
    cors_allow_all: bool = False
    # Nginx가 deploy/nginx-snippet.conf 처럼 CORS 헤더를 붙이면 true (앱에서 CORS 끔, 헤더 중복 방지).
    use_nginx_cors: bool = False

    # true면 Cursor CLI 없이 즉시 가짜 답변 (Flutter·CORS·POST 파이프만 테스트할 때).
    mock_agent: bool = False


settings = Settings()
