"""
Entry-point для запуска MVP_1H Scanner.

Перехватывает ошибки импорта/компиляции (SyntaxError, ImportError и т.д.)
и пишет их в logs/startup.log — потому что uvicorn настраивает логирование
только ПОСЛЕ успешного импорта приложения, и ошибки на этом этапе теряются.
"""

import sys
import logging
from pathlib import Path

BASE_DIR = Path(__file__).parent
STARTUP_LOG = BASE_DIR / "logs" / "startup.log"

# --- Минимальный logger для ошибок на этапе импорта ---
startup_logger = logging.getLogger("startup")
startup_logger.setLevel(logging.DEBUG)

# Всегда пишем в stderr (видно в консоли)
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setLevel(logging.ERROR)
_stderr.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
startup_logger.addHandler(_stderr)

# Пишем в файл только если он доступен
try:
    STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    _file = logging.FileHandler(STARTUP_LOG, mode="a", encoding="utf-8")
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    startup_logger.addHandler(_file)
except Exception:
    pass  # если лог-файл недоступен — просто пишем в stderr

# --- Попытка импорта приложения ---
try:
    import uvicorn
    from uvicorn.config import LOGGING_CONFIG
except ImportError:
    startup_logger.critical("uvicorn не установлен. Выполните: pip install uvicorn")
    sys.exit(1)

try:
    # Импорт именно здесь — чтобы поймать SyntaxError / ImportError
    from backend.app import app
except Exception as exc:
    startup_logger.critical("Ошибка импорта backend.app: %s", exc, exc_info=True)
    sys.exit(1)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MVP_1H Scanner Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--reload", action="store_true", help="Dev mode с auto-reload")
    args = parser.parse_args()

    log_config = str(BASE_DIR / "app_logging_config.yaml")

    if args.reload:
        # В dev-режиме uvicorn управляет логированием через --log-config
        uvicorn.run(
            "backend.app:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_config=log_config,
        )
    else:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_config=log_config,
        )


if __name__ == "__main__":
    main()
