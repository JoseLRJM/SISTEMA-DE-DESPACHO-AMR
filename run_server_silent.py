import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

APP_HOST = "0.0.0.0"
APP_PORT = 8000
BROWSER_HOST = "127.0.0.1"
APP_URL = f"http://{BROWSER_HOST}:{APP_PORT}"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "portable.log"


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def open_browser_delayed():
    time.sleep(2)
    webbrowser.open(APP_URL)


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    setup_logging()
    logging.info("Iniciando SistemaDespachoLite portable")
    logging.info("sys.executable=%s", sys.executable)
    logging.info("cwd=%s", str(Path.cwd()))
    logging.info("frozen=%s", getattr(sys, "frozen", False))
    logging.info("_MEIPASS=%s", getattr(sys, "_MEIPASS", None))
    logging.info("LAN URL=http://%s:%s", get_local_ip(), APP_PORT)

    try:
        project_dir = Path(__file__).resolve().parent
        if str(project_dir) not in sys.path:
            sys.path.insert(0, str(project_dir))

        from main import app

        threading.Thread(target=open_browser_delayed, daemon=True).start()

        config = uvicorn.Config(
            app=app,
            host=APP_HOST,
            port=APP_PORT,
            log_level="info",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception:
        logging.exception("Error al iniciar el servidor")


if __name__ == "__main__":
    main()
