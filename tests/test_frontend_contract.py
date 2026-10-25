import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_frontend_modules_and_preloaded_fonts_resolve_after_merge():
    with TestClient(create_app(Settings(_env_file=None))) as api:
        html = api.get("/").text
        paths = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        visited = set()
        while paths:
            path = paths.pop()
            if path in visited:
                continue
            visited.add(path)
            response = api.get(path)
            assert response.status_code == 200, path
            assert not response.headers["content-type"].startswith("text/html"), path
            if path.endswith(".js"):
                imports = re.findall(r'from\s+["\'](\.[^"\']+)["\']', response.text)
                paths.extend(urljoin(path, imported) for imported in imports)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js проверяет JS-контракт в CI")
def test_frontend_handles_every_source_status_from_the_api_contract():
    with TestClient(create_app(Settings(_env_file=None))) as api:
        schema = api.get("/openapi.json").json()["components"]["schemas"]
        statuses = schema["Source"]["properties"]["status"]["enum"]
        assert schema["SourceView"]["properties"]["status"]["enum"] == statuses
    script = """
      import fs from 'node:fs';
      import assert from 'node:assert/strict';
      const code = fs.readFileSync(process.argv[1], 'utf8');
      const {sourceStatusLabel, sourceNeedsAttention} = await import(
        'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
      );
      const unknown = sourceStatusLabel('future-status');
      for (const status of JSON.parse(fs.readFileSync(0, 'utf8'))) {
        const label = sourceStatusLabel(status);
        assert.equal(typeof label, 'string');
        assert.notEqual(label, unknown, status);
        assert.ok(label.toLowerCase().length, status);
      }
      assert.equal(typeof unknown, 'string');
      assert.equal(sourceNeedsAttention('partial'), true);
    """
    module = Path(__file__).resolve().parents[1] / "frontend/static/js/source-status.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module)],
        input=json.dumps(statuses),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
