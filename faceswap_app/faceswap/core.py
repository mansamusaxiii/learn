"""Core face-swap pipeline: face detection (insightface buffalo_l) +
face swapping (inswapper_128) applied frame-by-frame to a video, with the
original audio track re-muxed back in via ffmpeg.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import cv2
import insightface
import numpy as np

_face_analyser = None
_swapper = None


def get_face_analyser():
    global _face_analyser
    if _face_analyser is None:
        _face_analyser = insightface.app.FaceAnalysis(name="buffalo_l")
        _face_analyser.prepare(ctx_id=0, det_size=(640, 640))
    return _face_analyser


def get_swapper():
    global _swapper
    if _swapper is None:
        model_path = Path.home() / ".insightface" / "models" / "inswapper_128.onnx"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Swapper model not found at {model_path}. "
                "Run `python download_models.py` first."
            )
        _swapper = insightface.model_zoo.get_model(str(model_path))
    return _swapper


def get_largest_face(faces):
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def get_source_face(image_path: str):
    analyser = get_face_analyser()
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read source image: {image_path}")
    faces = analyser.get(img)
    face = get_largest_face(faces)
    if face is None:
        raise ValueError("No face detected in the source image.")
    return face


def swap_faces_in_frame(frame: np.ndarray, source_face, analyser, swapper) -> np.ndarray:
    faces = analyser.get(frame)
    if not faces:
        return frame
    result = frame
    for face in faces:
        result = swapper.get(result, face, source_face, paste_back=True)
    return result


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def process_video(
    source_image_path: str,
    target_video_path: str,
    output_path: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Swap the face from `source_image_path` onto every face detected in
    each frame of `target_video_path`, writing the result to `output_path`.
    Returns the output path.
    """
    analyser = get_face_analyser()
    swapper = get_swapper()
    source_face = get_source_face(source_image_path)

    cap = cv2.VideoCapture(target_video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open target video: {target_video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    with tempfile.TemporaryDirectory() as tmp_dir:
        silent_path = str(Path(tmp_dir) / "silent.mp4")
        writer = cv2.VideoWriter(
            silent_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
        )

        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            swapped = swap_faces_in_frame(frame, source_face, analyser, swapper)
            writer.write(swapped)
            idx += 1
            if progress_cb:
                progress_cb(idx, frame_count)
        cap.release()
        writer.release()

        if _ffmpeg_available():
            cmd = [
                "ffmpeg", "-y",
                "-i", silent_path,
                "-i", target_video_path,
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0?",
                "-c:a", "aac",
                "-shortest",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
        else:
            shutil.copy(silent_path, output_path)

    return output_path
