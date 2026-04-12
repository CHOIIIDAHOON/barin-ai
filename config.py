from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_APP_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 기본은 env가 .env보다 우선 → SSH(SendEnv)로 Mac의 CURSOR_* 가 넘어오면 서버 .env가 무시됨.
        # 배포 시에는 보통 .env가 진실이므로 dotenv를 env보다 먼저 두어 .env가 이김.
        return (
            init_settings,
            dotenv_settings,
            env_settings,
            file_secret_settings,
        )

    # Directory the Cursor agent uses as the project root (must exist on the server).
    cursor_project_dir: str = "/var/cursor-project"
    # `cursor` (runs `cursor agent …`) or standalone `agent` binary (basename agent → no extra subcommand).
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
