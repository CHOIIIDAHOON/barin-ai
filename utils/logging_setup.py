"""앱 로그를 프로젝트 루트의 logs/ 디렉터리에 기록한다."""

import logging
import os
from pathlib import Path


def setup_app_logging(app_root: Path, *, log_dir_name: str = "logs") -> Path:
    """
    chatbot_api 로거에 logs/app.log FileHandler를 붙인다(중복 등록 방지).
    콘솔(uvicorn 루트 로거)로의 전파는 그대로 둔다.
    """
    log_dir = (app_root / log_dir_name).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "app.log").resolve()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger("chatbot_api")
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            bf = getattr(h, "baseFilename", None)
            if bf and os.path.normcase(os.path.abspath(bf)) == os.path.normcase(str(log_file)):
                return log_dir

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return log_dir
