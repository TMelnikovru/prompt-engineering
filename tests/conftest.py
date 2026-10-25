import json
import os
import select
import shutil
import subprocess
from pathlib import Path

import pytest


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
            ready, _, _ = select.select([process.stdout], [], [], 5)
            assert ready, "Mock server did not start"
            line = process.stdout.readline()
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
