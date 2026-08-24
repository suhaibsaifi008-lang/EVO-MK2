"""EVO MK2 voice agent - Pipecat pipeline (Phase 7.8).

Fully local/free stack:
    Browser mic -> SmallWebRTC -> Silero VAD -> faster-whisper small.en
    -> FreeLLMAPI (gpt-oss-120b class, OpenAI-compatible)
    -> Kokoro TTS (local neural, af_heart) -> WebRTC speakers

Run:
    py -3 voice_bot.py --transport webrtc
Then open http://localhost:7860/client/ and click Connect.

First TTS use downloads Kokoro ONNX (~310 MB) once into ~/.cache/pipecat.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loguru  # noqa: F401  (pipecat configures it)

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport

# EVO config (.env loader, persona, truth law)
from mk2 import tools as evo_tools
from mk2.persona_loader import ensure_persona, truth_law
from mk2.config import settings as evo_settings

TOOL_NAMES = ("web_search", "deep_thought", "task_start",
              "reminder_add_tool", "youtube_summarize", "docs_create")


def _tool_fn(name: str):
    async def _fn(**kwargs):
        try:
            result = await asyncio.to_thread(evo_tools.call, name, kwargs)
            return str(result.get("speech", ""))[:900]
        except Exception as exc:  # noqa: BLE001
            return f"Tool failed: {str(exc)[:150]}"
    _fn.__name__ = name
    _fn.__doc__ = next((t["description"] for t in evo_tools.manifest()
                        if t["name"] == name), name)
    return _fn


def _system_instruction() -> str:
    ensure_persona()
    from mk2.persona_loader import persona_block

    tool_lines = "\n".join(f"- {n}" for n in TOOL_NAMES)
    return (
        persona_block(max_chars=700)
        + "\n" + truth_law()
        + "\nYou are speaking aloud on a phone-like voice channel. Keep "
        "replies SHORT (1-4 sentences), natural spoken language, no lists, "
        "no markdown, no emojis. You can call these tools when needed:\n"
        + tool_lines
    )


async def run_bot(transport: SmallWebRTCTransport,
                  runner_args: RunnerArguments) -> None:
    whisper_model = os.environ.get("EVO_STT_MODEL", "small.en")

    stt = WhisperSTTService(model=whisper_model, compute_type="int8")
    tts = KokoroTTSService(settings=KokoroTTSService.Settings(
        voice="af_heart"))

    llm = OpenAILLMService(
        api_key=evo_settings.openai_key,
        base_url=evo_settings.openai_base,
        model=evo_settings.openai_model or "gpt-oss-120b",
        params=None,
        system_instruction=_system_instruction(),
    )

    context = LLMContext(messages=[], tools=[_tool_fn(n) for n in TOOL_NAMES])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_connected")
    async def _connected(transport, client):
        await task.queue_frames([context.aggregate()])
        print("[voice] client connected")

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(transport, client):
        print("[voice] client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint,
                            name="EVOVoice")
    await runner.run(task)


async def bot(runner_args: RunnerArguments) -> None:
    transport = await create_transport(runner_args)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
