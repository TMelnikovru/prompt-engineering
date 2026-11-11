"""Обновление снимка контракта. Запускать из корня проекта."""

import argparse
import json
from pathlib import Path

from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    target = Path("docs/openapi.json")
    rendered = json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n"
    if options.check:
        if not target.exists() or target.read_text() != rendered:
            raise SystemExit("OpenAPI изменился. Выполните uv run python -m scripts.export_openapi")
        print("OpenAPI актуален")
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(rendered)
    print(f"Обновлён {target}")


if __name__ == "__main__":
    main()
