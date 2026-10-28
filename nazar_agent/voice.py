"""Voice messages to text with Muse Spark.

Muse Voice Transcribe covers 25 languages, not Russian or Uzbek, so a
voice message goes to Muse Spark 1.2 as audio input instead
(https://dev.meta.ai/docs/video-understanding). That input takes WAV or MP3;
Telegram sends Ogg/Opus, so it is decoded here first (PyAV bundles ffmpeg).
"""

from __future__ import annotations

import base64
import io
import wave

import av

from nazar_agent.llm import LLMClient
from nazar_agent.usage import count

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # 16-bit
MAX_SECONDS = 120
# Reasoning shares the output budget; with less, the transcript can come back
# empty (per Meta's docs).
TRANSCRIBE_MAX_OUTPUT_TOKENS = 4000

INSTRUCTIONS = """\
Transcribe this voice message exactly as spoken. It is from a business owner
in Uzbekistan and is usually in Russian or Uzbek, sometimes mixing both.
Keep the original language and words; do not translate or answer it.
Return only the transcript.
"""


class VoiceError(Exception):
    """The voice message cannot be used; the message says why, for the person."""


def to_wav(audio: bytes) -> bytes:
    """Any audio container ffmpeg reads (Telegram's Ogg/Opus) → mono 16 kHz 16-bit WAV."""
    try:
        container = av.open(io.BytesIO(audio))
    except av.error.FFmpegError as exc:
        raise VoiceError("Не получилось прочитать голосовое сообщение.") from exc

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    pcm = bytearray()
    with container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                pcm += _samples(converted)
        for converted in resampler.resample(None):  # flush what the resampler holds
            pcm += _samples(converted)

    if not pcm:
        raise VoiceError("Голосовое сообщение пустое.")
    if len(pcm) > MAX_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE:
        raise VoiceError(f"Голосовое длиннее {MAX_SECONDS // 60} минут — запишите короче.")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(BYTES_PER_SAMPLE)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(bytes(pcm))
    return buffer.getvalue()


def _samples(frame: av.AudioFrame) -> bytes:
    # Mono s16 is one packed plane; the buffer may be padded past the samples.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]


async def transcribe(llm: LLMClient, model: str, audio: bytes) -> str:
    wav = to_wav(audio)
    turn = await llm.respond(
        model=model,
        instructions=INSTRUCTIONS,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Transcribe this voice message."},
                {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}},
            ],
        }],
        tools=[],
        max_output_tokens=TRANSCRIBE_MAX_OUTPUT_TOKENS,
    )
    count("voice", model, turn.input_tokens, turn.output_tokens)
    text = turn.text.strip()
    if not text:
        raise VoiceError("Не расслышал — повторите, пожалуйста, или напишите текстом.")
    return text
