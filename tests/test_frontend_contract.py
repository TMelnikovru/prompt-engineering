import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_merged_frontend_views_and_required_elements_exist():
    with TestClient(create_app(Settings(_env_file=None))) as api:
        html = BeautifulSoup(api.get("/").text, "html.parser")
        ids = [element["id"] for element in html.select("[id]")]
        assert len(ids) == len(set(ids)), "Merge must not duplicate form/view IDs"
        app_js = api.get("/static/js/app.js").text
        views = json.loads(re.search(r"const MAIN_VIEWS = (\[.*?\]);", app_js)[1])
        assert set(views) <= {element["data-view"] for element in html.select("[data-view]")}
        assert {element["data-nav"] for element in html.select("[data-nav]")} <= set(views)
        for path in ("app.js", "dsba.js"):
            js = api.get("/static/js/" + path).text
            selectors = re.findall(r'(?:\$|querySelector)\("#([\w-]+)"\)', js)
            assert set(selectors) <= set(ids), set(selectors) - set(ids)


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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js проверяет JS-контракт в CI")
def test_summary_keeps_fake_data_isolated_and_discards_previous_profile_responses():
    with TestClient(create_app(Settings(_env_file=None, demo_mode=False))) as api:
        original = api.get("/api/v1/dashboard?data_source=fake").json()
        completed = original["tasks"][0]["id"]
        updated = api.get(
            "/api/v1/dashboard",
            params={"data_source": "fake", "available_minutes": 30, "completed": completed},
        ).json()
    script = """
      import fs from 'node:fs';
      import assert from 'node:assert/strict';
      const code = fs.readFileSync(process.argv[1], 'utf8');
      const {createSummaryLoader} = await import(
        'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
      );
      const [original, updated] = JSON.parse(fs.readFileSync(0, 'utf8'));
      let dashboard = original, source = 'fake', calls = 0, loading = false;
      let respond, reject, output, error;
      const loader = createSummaryLoader({
        getDashboard: () => dashboard,
        getDataSource: () => source,
        fetchSummary: () => {
          calls++;
          return new Promise((resolve, fail) => { respond = resolve; reject = fail; });
        },
        onLoading: value => { loading = value; },
        onResult: value => { output = value; },
        onError: value => { error = value; },
      });
      await loader.load();
      assert.equal(calls, 0, 'Fake must never fetch the live knowledge summary');
      assert.equal(output.fake, true);
      dashboard = updated;
      loader.invalidate();
      await loader.load();
      assert.ok(output.summary.summary.includes('из доступных 30 мин'));
      assert.equal(output.summary.highlights.length, updated.plan.blocks.length);
      assert.equal(calls, 0);

      source = 'live';
      loader.invalidate();
      const previousProfile = loader.load();
      assert.equal(loading, true);
      source = 'fake';
      loader.invalidate();
      await loader.load();
      const fakeResult = output;
      respond({summary: {summary: 'private live text', highlights: []}});
      await previousProfile;
      assert.equal(output, fakeResult, 'Late response belongs to the previous profile');
      assert.equal(loading, false);

      source = 'live';
      loader.invalidate();
      const failed = loader.load();
      reject(new Error('Summary unavailable'));
      await failed;
      assert.equal(loader.loaded, false, 'Failure must remain retryable');
      assert.equal(loading, false);
      assert.equal(error.message, 'Summary unavailable');
      const retry = loader.load();
      respond({summary: null});
      await retry;
      assert.equal(output.summary, null);
      assert.equal(loader.loaded, true);
    """
    module = Path(__file__).resolve().parents[1] / "frontend/static/js/summary.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module)],
        input=json.dumps([original, updated]),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
