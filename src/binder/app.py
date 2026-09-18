import os
from http.server import ThreadingHTTPServer

from .binder import Binder


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("BINDER_HOST", "127.0.0.1")
    port = port or int(os.environ.get("BINDER_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Binder)
    print(f"Binder running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
