"""Локальный запуск сайта на Windows с совместимым с psycopg циклом событий.

`python -m app.serve [--port 8000]`. В Docker и на Linux используйте uvicorn напрямую.
"""

import argparse

import uvicorn

from app.runtime import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config("app.main:app", host=args.host, port=args.port))
    run(server.serve())


if __name__ == "__main__":
    main()
