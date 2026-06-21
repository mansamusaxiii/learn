"""Download the model weights needed by the face-swap app.

Run this once before starting app.py:

    python download_models.py

It fetches:
  - buffalo_l: face detection/recognition models (via insightface, cached
    automatically in ~/.insightface)
  - inswapper_128.onnx: the face-swap model, downloaded from the Hugging
    Face Hub mirror and cached in ~/.insightface/models
"""
import os
from pathlib import Path

from huggingface_hub import hf_hub_download
import insightface

MODELS_DIR = Path.home() / ".insightface" / "models"
SWAPPER_REPO = "ezioruan/inswapper_128.onnx"
SWAPPER_FILE = "inswapper_128.onnx"


def download_swapper():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODELS_DIR / SWAPPER_FILE
    if dest.exists():
        print(f"Swapper model already present at {dest}")
        return dest
    print(f"Downloading {SWAPPER_FILE} from {SWAPPER_REPO} ...")
    path = hf_hub_download(repo_id=SWAPPER_REPO, filename=SWAPPER_FILE)
    dest.write_bytes(Path(path).read_bytes())
    print(f"Saved to {dest}")
    return dest


def download_face_analysis():
    print("Downloading/initializing buffalo_l face analysis models ...")
    app = insightface.app.FaceAnalysis(name="buffalo_l")
    app.prepare(ctx_id=0, det_size=(640, 640))
    print("buffalo_l ready.")


if __name__ == "__main__":
    download_swapper()
    download_face_analysis()
    print("All models downloaded.")
