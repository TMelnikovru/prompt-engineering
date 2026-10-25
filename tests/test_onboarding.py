import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.config import Settings
from app.connectors.dsba import DsbaCatalog
from app.main import create_app


def test_login_collects_dsba_selection_without_manual_connection_form():
    with TestClient(create_app(Settings(_env_file=None, demo_mode=False))) as api:
        html = BeautifulSoup(api.get("/").text, "html.parser")
        assert html.body["data-demo-mode"] == "false"
        login = html.select_one("#signup-form")
        assert login.select_one('input[name="name"]')
        for name in ("program", "year", "group", "sub"):
            assert login.select_one(f'select[name="{name}"]'), name
        assert login.select_one("#signup-subject-options")
        assert login.select_one("#signup-retry")
        assert html.select_one("#edit-profile-button")
        assert not html.select_one("#dsba-form")
        assert not html.select_one("#dsba-connection")
    with TestClient(create_app(Settings(_env_file=None, demo_mode=True))) as api:
        assert 'data-demo-mode="true"' in api.get("/").text


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js нужен для JS-проверок в CI")
def test_profile_migration_and_catalog_boundaries():
    catalog = DsbaCatalog.model_validate(
        {
            "academic_year": "2026/2027",
            "programs": [
                {
                    "id": "dsba",
                    "name": "DSBA",
                    "title": "DSBA",
                    "years": [
                        {
                            "year": 1,
                            "label": "1 курс",
                            "groups": ["261", "262"],
                            "subgroups": ["1", "2"],
                            "subjects": ["cpp", "calculus"],
                            "names": {"cpp": "C++", "calculus": "Calculus"},
                        },
                        {
                            "year": 2,
                            "label": "2 курс",
                            "groups": [],
                            "subgroups": [],
                            "subjects": [],
                            "names": {},
                        },
                    ],
                }
            ],
        }
    )
    script = """
      import fs from 'node:fs';
      import assert from 'node:assert/strict';
      const code = fs.readFileSync(process.argv[1], 'utf8');
      const {normalizeName, completeProfile, savedSelection, selectionFromCatalog} = await import(
        'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
      );
      const catalog = JSON.parse(fs.readFileSync(0, 'utf8'));
      const selection = {program:'dsba', year:1, group:'261', sub:'', subjects:[]};
      const old = {name:'Тестовый Студент', group:'261'};
      const choice = {source_id:'dsba:' + 'a'.repeat(32), selection};
      assert.equal(completeProfile(old, null), false); // legacy name/group need DSBA choices
      assert.equal(completeProfile(old, savedSelection(old, choice)), true);
      assert.equal(normalizeName(null), '');
      assert.equal(normalizeName(' Тестовый   Студент '), old.name);
      const current = {name:old.name, dsba:choice};
      const legacy = {...choice, source_id:'dsba:'+'b'.repeat(32)};
      assert.deepEqual(savedSelection(current, legacy), choice);
      assert.equal(savedSelection({...current, dsba:{}}, choice), null);
      assert.equal(completeProfile({name:'Однослово'}, choice), false);
      assert.deepEqual(selectionFromCatalog(catalog, {...selection, year:'1'}), selection);
      assert.equal(selectionFromCatalog(catalog, {...selection, group:'invented'}), null);
      assert.equal(selectionFromCatalog(catalog, {...selection, sub:'9'}), null);
      assert.equal(selectionFromCatalog(catalog, {...selection, subjects:['unknown']}), null);
      const custom = selectionFromCatalog(catalog, {...selection, subjects:['cpp','cpp']});
      assert.deepEqual(custom, {...selection, subjects:['cpp']});
      const general = {...selection, year:2, group:''};
      assert.deepEqual(selectionFromCatalog(catalog, general), general);
      assert.equal(selectionFromCatalog(catalog, {...general, group:'261'}), null);
    """
    module = Path(__file__).resolve().parents[1] / "frontend/static/js/dsba-profile.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module)],
        input=catalog.model_dump_json(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
