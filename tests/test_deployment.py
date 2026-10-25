import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("bash") is None, reason="Deployment requires Bash")
def test_preview_cron_preserves_production_and_does_not_duplicate_itself(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    crontab = commands / "crontab"
    crontab.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = -l ]; then\n'
        '  cat "$HSE_TEST_CRONTAB"\n'
        "else\n"
        '  cp "$1" "$HSE_TEST_CRONTAB"\n'
        "fi\n"
    )
    crontab.chmod(0o755)
    saved_cron = tmp_path / "crontab"
    saved_cron.write_text("0 3 * * * /bin/true # unrelated-job\n")
    env = {
        **os.environ,
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        "HSE_TEST_CRONTAB": str(saved_cron),
        "HSE_DEPLOY_PROJECT": "hse-study-agent",
    }
    for name in ("production", "preview", "preview"):
        deploy_root = tmp_path / name
        (deploy_root / "repository" / ".git").mkdir(parents=True, exist_ok=True)
        (deploy_root / ".env").touch()
        (deploy_root / "deploy.sh").touch()
        if name == "preview":
            (deploy_root / "deploy.env").write_text(
                "HSE_DEPLOY_BRANCH=backend/postgresql\n"
                "HSE_DEPLOY_PROJECT=hse-study-agent-postgresql\n"
            )
        subprocess.run(
            ["bash", str(ROOT / "deploy/install-cron.sh"), str(deploy_root)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    lines = saved_cron.read_text().splitlines()
    assert len(lines) == 3
    assert lines[0] == "0 3 * * * /bin/true # unrelated-job"
    assert sum(line.endswith("# hse-study-agent-autodeploy") for line in lines) == 1
    assert sum(line.endswith("# hse-study-agent-postgresql-autodeploy") for line in lines) == 1
    assert any(str(tmp_path / "production" / "deploy.sh") in line for line in lines)
    assert any(str(tmp_path / "preview" / "deploy.sh") in line for line in lines)
