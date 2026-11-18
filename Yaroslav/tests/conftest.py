import json
import os
import select
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.db.url import postgresql_url

if sys.platform == "win32":
    import asyncio

    # Асинхронный psycopg не работает с ProactorEventLoop, который Windows даёт по умолчанию.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def first_line(process, timeout: float) -> str:
    """Первая строка stdout с таймаутом. select() на Windows не работает с каналами."""
    if sys.platform != "win32":
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        assert ready, "Mock server did not start"
        return process.stdout.readline()
    import queue
    import threading

    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError("Mock server did not start") from None


@pytest.fixture
def ingestion_url():
    value = os.getenv("HSE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Нужна отдельная PostgreSQL БД через HSE_TEST_DATABASE_URL")
    if not postgresql_url(value).database.endswith("_test"):
        pytest.fail("Разрешена только отдельная БД с суффиксом _test")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "HSE_DATABASE_URL": value},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return value


@pytest.fixture
def upstreams():
    """Настоящие HTTP-соединения с теми же handlers, что используются Postman simulation."""
    if not shutil.which("node"):
        pytest.skip("Для HTTP-симуляций установите Node.js (в CI он есть)")
    processes = []
    urls = {}
    try:
        for name in ("fkn-wiki", "openai", "dsba"):
            path = Path("postman/mocks") / name / "default.js"
            process = subprocess.Popen(
                ["node", str(path)],
                env={**os.environ, "PORT": "0"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            line = first_line(process, timeout=5)
            assert line, "Mock server exited before listening"
            port = json.loads(line)["port"]
            urls[name] = f"http://127.0.0.1:{port}"
        yield urls
    finally:
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
