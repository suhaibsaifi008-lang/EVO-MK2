"""EVO MK2 voice agent - standalone Pipecat runner (dev fallback).

The integrated channel lives in mk2/voice/webrtc_v2.py and starts
automatically with the kernel:

    py -3 run.py            ->  console + http://127.0.0.1:8421/voice/client/

This launcher only exists for the pipecat dev-runner workflow:

    py -3 voice_bot.py --transport webrtc   ->  http://localhost:7860/client/

Fully local/free stack:
    Browser mic -> SmallWebRTC -> Silero VAD -> faster-whisper small.en
    -> FreeLLMAPI fastest-stable route -> Kokoro TTS -> WebRTC speakers

First TTS use downloads Kokoro ONNX (~310 MB) once into ~/.cache/pipecat.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loguru  # noqa: F401  (pipecat configures it)

from mk2.voice.webrtc_v2 import bot  # noqa: F401  (runner discovers `bot`)

if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
