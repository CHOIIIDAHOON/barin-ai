from pathlib import Path

from pydantic import Field
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
    # 에이전트가 끝나기 전 주기적으로 서버 로그에 남기는 간격(초). 0이면 주기 로그 끔(대기만).
    agent_heartbeat_log_sec: float = 20.0
    # POST /warmup 최대 대기(초). 첫 CLI 기동이 길 수 있어 기본은 여유 있게 둠.
    warmup_timeout_sec: int = 300
    # Optional: require this Bearer token on POST /chat (empty = no HTTP auth).
    chat_api_secret: str = ""
    # Cursor CLI --model (run: cursor agent --list-models). Default is Composer fast tier.
    # Override with e.g. auto, gpt-5.4-mini-medium, gemini-3-flash for cost/latency tradeoffs.
    cursor_model: str = "composer-2-fast"
    # Optional: cursor agent --mode. "ask" = read-only Q&A (no edits), usually lower latency than full agent.
    cursor_agent_mode: str = ""
    # 서버 무인 실행 시 필수에 가깝다. 미설정이면 첫 실행이 로그인/인증 대기로 끝까지 멈출 수 있음 (값은 로그에 노출하지 말 것).
    cursor_api_key: str = Field(default="", validation_alias="CURSOR_API_KEY")
    # true면 CLI --force (도구/쉘 자동 허용). 승인 대기로 멈출 때 켜되 저장소에 쓰기·명령 실행이 가능해진다.
    cursor_agent_force: bool = Field(default=False, validation_alias="CURSOR_AGENT_FORCE")

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

    # --- Async jobs + worker (POST /chat/jobs) ---
    # In-memory job history cap per uvicorn worker process.
    agent_job_store_max: int = 500
    # Concurrent queue consumers (each run still spawns one cursor CLI). Same repo: 1 recommended.
    agent_worker_concurrency: int = 1
    # HTTP POST timeout when delivering job webhooks (seconds).
    job_webhook_timeout_sec: float = 30.0

    # --- Streaming (POST /chat/stream) ---
    # Max bytes per read from agent stdout when streaming.
    agent_stream_read_chunk_bytes: int = 4096


settings = Settings()
