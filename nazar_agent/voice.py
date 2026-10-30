"""Voice messages to text with Muse Spark.

Muse Voice Transcribe covers 25 languages, not Russian or Uzbek, so a
voice message goes to Muse Spark 1.2 as audio input instead
(https://dev.meta.ai/docs/video-understanding). That input takes WAV or MP3;
Telegram sends Ogg/Opus and a browser's microphone WebM/Opus or MP4/AAC, so
it is decoded here first (PyAV bundles ffmpeg).
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
in Uzbekistan: in Russian, Uzbek or English, sometimes mixing them. Write
every word in the language it was spoken in; do not translate or answer it.
Return only the transcript.
"""
# The window's language, when known: which language to expect first.
LIKELY = "The owner's window is in {language}, so expect {language} first.\n"
LANGUAGES = {"ru": "Russian", "uz": "Uzbek", "en": "English"}
ASK = "Transcribe this voice message."
# What the model says when it heard no speech (a tone, silence): not a question to put in the box.
NOT_SPEECH = {"transcribe this voice message", "no audio received to transcribe", "no audio received"}


MESSAGES: dict[str, dict[str, str]] = {
    "unreadable": {
        "ru": "Не получилось прочитать голосовое сообщение.",
        "uz": "Ovozli xabarni o'qib bo'lmadi.",
        "en": "The voice message could not be read.",
    },
    "empty": {
        "ru": "Голосовое сообщение пустое.",
        "uz": "Ovozli xabar bo'sh.",
        "en": "The voice message is empty.",
    },
    "too_long": {
        "ru": f"Голосовое длиннее {MAX_SECONDS // 60} минут — запишите короче.",
        "uz": f"Ovozli xabar {MAX_SECONDS // 60} daqiqadan uzun — qisqaroq yozing.",
        "en": f"The voice message is longer than {MAX_SECONDS // 60} minutes — record a shorter one.",
    },
    "unheard": {
        "ru": "Не расслышал — повторите, пожалуйста, или напишите текстом.",
        "uz": "Eshitmadim — iltimos, takrorlang yoki matn bilan yozing.",
        "en": "I could not hear it — say it again, please, or type it.",
    },
}


class VoiceError(Exception):
    """The voice message cannot be used; the message says why, for the person (in Russian, or ``said(lang)``)."""

    def __init__(self, kind: str) -> None:
        super().__init__(MESSAGES[kind]["ru"])
        self.kind = kind

    def said(self, lang: str) -> str:
        return MESSAGES[self.kind].get(lang, MESSAGES[self.kind]["ru"])


def to_wav(audio: bytes) -> bytes:
    """Any audio container ffmpeg reads (Telegram's Ogg/Opus) → mono 16 kHz 16-bit WAV."""
    try:
        container = av.open(io.BytesIO(audio))
    except av.error.FFmpegError as exc:
        raise VoiceError("unreadable") from exc

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    pcm = bytearray()
    with container:
        if not container.streams.audio:
            raise VoiceError("unreadable")
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                pcm += _samples(converted)
        for converted in resampler.resample(None):  # flush what the resampler holds
            pcm += _samples(converted)

    if not pcm:
        raise VoiceError("empty")
    if len(pcm) > MAX_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE:
        raise VoiceError("too_long")

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


async def transcribe(llm: LLMClient, model: str, audio: bytes, lang: str | None = None) -> str:
    """``lang`` — the window's language, a hint; the bot does not know one."""
    wav = to_wav(audio)
    likely = LIKELY.format(language=LANGUAGES[lang]) if lang in LANGUAGES else ""
    turn = await llm.respond(
        model=model,
        instructions=INSTRUCTIONS + likely,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": ASK},
                {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}},
            ],
        }],
        tools=[],
        max_output_tokens=TRANSCRIBE_MAX_OUTPUT_TOKENS,
        role="voice",
    )
    count("voice", model, turn.input_tokens, turn.output_tokens)
    text = turn.text.strip()
    if not text or text.strip(" .!\"'«»").casefold() in NOT_SPEECH:
        raise VoiceError("unheard")
    return text
