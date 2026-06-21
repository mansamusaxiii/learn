# Face Swap Video

A small personal-use Gradio app that swaps a face from a photo onto every
face detected in a video, using [InsightFace](https://github.com/deepinsight/insightface)'s
`buffalo_l` detector and the `inswapper_128` swap model (the same model
used by popular open-source face-swap tools).

## Responsible use

Only use this with your own likeness or with the explicit, informed
consent of everyone whose face appears in the source image or target
video. Do not use it to impersonate people, harass anyone, or create
misleading media. You are responsible for complying with local laws and
the model's license terms.

## Setup

```bash
cd faceswap_app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# also requires the ffmpeg binary on PATH for audio muxing
# e.g. `apt install ffmpeg` / `brew install ffmpeg`

python download_models.py   # one-time download of model weights
```

If you have an NVIDIA GPU, install `onnxruntime-gpu` instead of
`onnxruntime` for much faster inference.

## Run

```bash
python app.py
```

Open the printed local URL, upload a source face photo and a target
video, and click **Swap Face**. The output video keeps the original
audio track (requires `ffmpeg`); without `ffmpeg` the output will be
silent.

## How it works

1. `download_models.py` fetches the `buffalo_l` face analysis models
   (via `insightface`, cached in `~/.insightface`) and the
   `inswapper_128.onnx` swap model from the Hugging Face Hub.
2. `faceswap/core.py` detects the largest face in the source image,
   then for every frame of the target video detects all faces and
   swaps the source face onto each of them.
3. The swapped frames are written to a silent video, then the original
   audio is muxed back in with `ffmpeg`.
