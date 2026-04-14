"""Cursor / standalone agent 실행에 쓰는 argv 목록을 만든다."""

from pathlib import Path
from typing import List

from config import settings


def is_standalone_agent(cli: str) -> bool:
    """~/.local/bin/agent 처럼 단독 바이너리면 True (cursor agent 서브커맨드 불필요)."""
    name = Path((cli or "").strip() or "cursor").name
    return name in ("agent", "agent.exe")


def build_cmd(prompt: str) -> List[str]:
    """settings 기준으로 cursor/agent CLI 인자 리스트를 구성한다."""
    cli = settings.cursor_cli_path.strip() or "cursor"
    if is_standalone_agent(cli):
        cmd = [cli, "--trust"]
    else:
        cmd = [cli, "agent", "--trust"]
    model = settings.cursor_model.strip()
    if model:
        cmd.extend(["--model", model])
    mode = (settings.cursor_agent_mode or "").strip().lower()
    if mode in ("ask", "plan"):
        cmd.extend(["--mode", mode])
    if settings.cursor_agent_force:
        cmd.append("--force")
    cmd.extend(["-p", prompt, "--output-format", "text"])
    return cmd
