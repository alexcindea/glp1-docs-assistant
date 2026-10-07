"""Write the API's OpenAPI schema to a file, without starting a server.

    uv run python -m app.export_openapi <output.json>

The web app generates its TypeScript API types from this file (`pnpm gen:api`).
"""

import json
import sys
from pathlib import Path

from app.config import Settings
from app.factory import create_app


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        sys.exit("usage: python -m app.export_openapi <output.json>")
    schema = create_app(Settings()).openapi()
    Path(argv[1]).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv)
