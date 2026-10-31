from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from dotabyss_ps.app import Settings, create_app
from .service_bootstrap import canonical_origin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    origin = canonical_origin(args.origin)
    base = Settings.from_directory(args.data_dir.resolve())
    settings = Settings(base.data_dir, base.fixtures_dir, base.resources_dir, new_game_schema=args.schema.resolve())
    app = create_app(settings)

    @app.middleware("http")
    async def identify(request, call_next):
        response = await call_next(request)
        if request.url.path == "/healthz":
            response.headers["X-DotAbyss-Service-Instance"] = args.instance
        return response

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=urlsplit(origin).port,
                                         access_log=False, timeout_graceful_shutdown=15))

    def control():
        while True:
            command = sys.stdin.buffer.readline(64)
            if not command or command.strip() == b"stop":
                server.should_exit = True
                return

    threading.Thread(target=control, name="service-control", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
