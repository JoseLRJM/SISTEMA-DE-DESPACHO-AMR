"""One process owns the in-memory runtime for a local SQLite deployment."""
import os
import sys
from pathlib import Path


def validate_worker_configuration(argv=None, environ=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    counts = [environ.get("WEB_CONCURRENCY", "1")]
    for i, value in enumerate(argv):
        if value in ("--workers", "-w"):
            counts.append(argv[i + 1] if i + 1 < len(argv) else "invalid")
        elif value.startswith("--workers="):
            counts.append(value.split("=", 1)[1])
    if any(str(value).strip() != "1" for value in counts):
        raise RuntimeError("Despacho AMR soporta únicamente 1 worker por base local; no iniciar varios workers o réplicas.")


class RuntimeLease:
    def __init__(self, database_path):
        self.path = Path(str(Path(database_path).resolve()) + ".runtime.lock")
        self.handle = None
        self.pid = None
        self.ready = False

    @property
    def owned(self):
        return self.handle is not None and self.pid == os.getpid()

    def acquire(self):
        if self.owned:
            return
        handle = open(self.path, "a+b")
        try:
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Otro proceso posee el runtime de esta base. Modo soportado: 1 worker.") from exc
        self.handle, self.pid = handle, os.getpid()

    def close(self):
        if self.handle is not None:
            self.handle.close()
        self.handle, self.pid = None, None
        self.ready = False


class RuntimeLeaseMiddleware:
    def __init__(self, app, lease):
        self.app, self.lease = app, lease

    async def __call__(self, scope, receive, send):
        # Fail closed also when someone disables the ASGI startup/lifespan.
        if scope["type"] in {"http", "websocket"} and not (self.lease.owned and self.lease.ready):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": b"Runtime no iniciado: se requiere lifespan y un solo worker."})
            return
        await self.app(scope, receive, send)
