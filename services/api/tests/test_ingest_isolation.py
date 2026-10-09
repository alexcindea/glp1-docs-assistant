"""The API process must never load Docling or PyTorch: they live in the worker only."""

import subprocess
import sys

HEAVY = ("docling", "docling_core", "torch", "rapidocr")


def test_api_import_does_not_load_the_parser_stack() -> None:
    # A fresh interpreter, because other tests in this session do import Docling.
    code = f"import sys, app.main; print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    command = [sys.executable, "-c", code]
    result = subprocess.run(command, capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.strip() == ""
