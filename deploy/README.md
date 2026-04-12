# 서버 배포 (Ubuntu + systemd + Nginx)

## 전제

- Ubuntu 22.04/24.04 등 (systemd)
- 서버에 **Cursor CLI**가 설치되어 `cursor agent`가 동작해야 함 ([cursor.com](https://cursor.com) Linux 앱/CLI)
- 헤드리스 인증용 **`CURSOR_API_KEY`** (대시보드 → API / CLI 키)

## 1. 코드 올리기

로컬에서 (저장소 루트 `chatbot-api` 기준):

```bash
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '.env' \
  ./chatbot-api/ user@YOUR_SERVER:/opt/chatbot-api/
```

서버에서 `.env`는 직접 만들고 넣습니다 (키 유출 방지).

## 2. 서버에서 한 번에 준비

```bash
sudo bash /opt/chatbot-api/deploy/bootstrap-ubuntu.sh
```

스크립트는 `cursor-chat` 사용자, `/var/cursor-project`, venv, `pip install`까지 수행합니다.  
이후 **반드시** `/opt/chatbot-api/.env`를 채웁니다. 예시는 루트의 `.env.example` 참고.

필수에 가까운 항목:

| 변수 | 설명 |
|------|------|
| `CURSOR_API_KEY` | 헤드리스 실행 |
| `CURSOR_PROJECT_DIR` | 에이전트 작업 디렉터리 (기본 `/var/cursor-project`) |
| `CURSOR_CLI_PATH` | `which cursor` 결과 전체 경로 권장 |
| `CHAT_API_SECRET` | 운영에서는 Bearer 토큰 설정 권장 |
| `USE_NGINX_CORS` | Nginx가 `deploy/nginx-snippet.conf`로 CORS 처리 시 `true` |

Flutter 웹 등에서 브라우저 CORS:

- Nginx 스니펫 사용 → `.env`에 `USE_NGINX_CORS=true`
- Uvicorn만 쓸 때 → `CORS_ALLOW_ORIGINS` 또는 내부 테스트용 `CORS_ALLOW_ALL=true`

## 3. systemd 등록

```bash
sudo cp /opt/chatbot-api/deploy/cursor-chat-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cursor-chat-api
sudo systemctl status cursor-chat-api
```

로그: `journalctl -u cursor-chat-api -f`

## 4. Nginx (443 프록시 + TLS)

Uvicorn은 **`127.0.0.1:8000`** 만 쓰고, 밖에는 **80·443** 만 연다 (예: `ufw allow 80,443/tcp`).

### 한 번에 쓸 수 있는 예시

- **`deploy/nginx-site-http.example.conf`** — **인증서 없이 80만** (먼저 띄워서 동작 확인·Certbot 준비에 유리)  
- **`deploy/nginx-site-https.example.conf`** — **Let’s Encrypt 인증서가 이미 있을 때** (80→443 리다이렉트 + TLS)  
  - `api.example.com` → 본인 도메인  
  - `ssl_certificate` 경로를 실제 도메인 폴더로 수정  
  - `include .../nginx-snippet.conf` → 저장소 **절대 경로**로 수정  

인증서가 아직 없으면 **HTTP 예시만** 켜 두고, `certbot`으로 발급한 뒤 HTTPS 예시로 갈아타면 됩니다.

### Let’s Encrypt (Certbot) 예시

DNS A 레코드가 이 서버를 가리킨 뒤:

```bash
sudo apt install -y certbot python3-certbot-nginx
# 사이트 설정을 먼저 80만으로 올리거나, certbot이 안내하는 대로 진행
sudo certbot --nginx -d api.example.com
```

인증서 경로는 보통 `/etc/letsencrypt/live/api.example.com/` 아래입니다. 예시 파일의 `ssl_certificate` 줄과 맞춥니다.

### 활성화

```bash
sudo cp /opt/chatbot-api/deploy/nginx-site-https.example.conf /etc/nginx/sites-available/barin-ai-api
sudo ln -sf /etc/nginx/sites-available/barin-ai-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

앱 `.env`: **`USE_NGINX_CORS=true`**  
에이전트 응답이 길 수 있으므로 스니펫의 **`proxy_read_timeout 600s`** 를 유지하는 것이 좋습니다.

외부에서 호출 URL 예: `https://api.example.com/chat`, `https://api.example.com/health`.

## 5. 동작 확인

서버에서:

```bash
curl -sS http://127.0.0.1:8000/health
```

`CHAT_API_SECRET`을 켠 경우:

```bash
curl -sS -X POST http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"ping"}]}'
```

## 문제 해결

- **`cursor_agent_failed` + Workspace Trust**  
  앱은 이미 `cursor agent --trust`를 붙입니다. Cursor CLI 버전을 최신으로 맞추세요 (`cursor agent update`).
- **`cursor` not found**  
  `.env`의 `CURSOR_CLI_PATH`에 실제 바이너리 전체 경로를 넣으세요.
- **권한**  
  `cursor-chat` 사용자가 `CURSOR_PROJECT_DIR`에 읽기/쓰기 가능한지 확인 (`bootstrap`이 `chown` 처리).
- **느림**  
  `.env`에서 `CURSOR_MODEL`, `CURSOR_AGENT_MODE=ask` 등은 루트 `config.py` / `.env.example` 참고.
