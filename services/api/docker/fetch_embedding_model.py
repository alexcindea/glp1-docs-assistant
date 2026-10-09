"""Build time only: download the app's embedding model into HF_HOME.

Usage: python fetch_embedding_model.py path/to/app/embedding_models.py

Loads that one file rather than importing the app, so the Docker layer that runs
this depends only on the model pins: a code change doesn't download ~1.2 GB again.
"""

import importlib.util
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

spec = importlib.util.spec_from_file_location("embedding_models", Path(sys.argv[1]))
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load {sys.argv[1]}")
pins = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pins  # dataclasses look their module up here
spec.loader.exec_module(pins)

model = pins.LOCAL_MODELS[pins.APP_MODEL]
snapshot_download(
    model.repo, revision=model.revision, allow_patterns=["*.json", "*.txt", "*.safetensors"]
)
print(f"fetched {model.repo}@{model.revision}")
