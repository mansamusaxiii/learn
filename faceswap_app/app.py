"""Gradio web app: upload a source face photo and a target video, get back
a video with the face swapped onto every detected face.

Run with: python app.py
Models must be downloaded first: python download_models.py
"""
import tempfile
from pathlib import Path

import gradio as gr

from faceswap.core import process_video

DISCLAIMER = (
    "Use this tool only with your own face or with the explicit consent of "
    "everyone whose likeness appears in the source image or target video. "
    "Do not use it to impersonate others or create misleading content."
)


def run_face_swap(source_image, target_video, progress=gr.Progress()):
    if source_image is None or target_video is None:
        raise gr.Error("Please provide both a source face image and a target video.")

    output_path = str(Path(tempfile.mkdtemp()) / "output.mp4")

    def cb(done, total):
        if total:
            progress(done / total, desc=f"Processing frame {done}/{total}")

    try:
        process_video(source_image, target_video, output_path, progress_cb=cb)
    except Exception as exc:
        raise gr.Error(str(exc))

    return output_path


with gr.Blocks(title="Face Swap Video") as demo:
    gr.Markdown("# Face Swap Video")
    gr.Markdown(DISCLAIMER)
    with gr.Row():
        source_image = gr.Image(type="filepath", label="Source face image")
        target_video = gr.Video(label="Target video")
    run_btn = gr.Button("Swap Face", variant="primary")
    output_video = gr.Video(label="Result")

    run_btn.click(
        fn=run_face_swap,
        inputs=[source_image, target_video],
        outputs=output_video,
    )

if __name__ == "__main__":
    demo.launch()
