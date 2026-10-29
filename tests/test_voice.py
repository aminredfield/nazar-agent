import base64
import io
import math
import struct
import wave
from datetime import UTC, datetime, time

import av
import pytest

from nazar_agent.telegram.schedule import parse_brief_time, seconds_until
from nazar_agent.voice import SAMPLE_RATE, VoiceError, to_wav, transcribe
from tests.fakes import ScriptedLLM, turn


def tone_wav(seconds: float = 1.0, rate: int = 44100) -> bytes:
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return buffer.getvalue()


def ogg_opus(wav: bytes) -> bytes:
    """What Telegram sends for a voice message."""
    out = io.BytesIO()
    with av.open(io.BytesIO(wav)) as source, av.open(out, "w", format="ogg") as target:
        stream = target.add_stream("libopus", rate=48000, layout="mono")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        for frame in source.decode(audio=0):
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
    return out.getvalue()


def test_telegram_voice_becomes_mono_16k_wav():
    wav = to_wav(ogg_opus(tone_wav(1.0)))

    with wave.open(io.BytesIO(wav)) as result:
        assert (result.getnchannels(), result.getframerate(), result.getsampwidth()) == (1, SAMPLE_RATE, 2)
        assert abs(result.getnframes() - SAMPLE_RATE) < SAMPLE_RATE * 0.1  # about one second


def test_unreadable_or_too_long_audio_is_refused_in_plain_words():
    with pytest.raises(VoiceError, match="прочитать"):
        to_wav(b"not audio at all")
    with pytest.raises(VoiceError, match="длиннее"):
        to_wav(tone_wav(121, rate=8000))


async def test_transcript_request_sends_wav_with_room_to_answer():
    llm = ScriptedLLM([turn(text="  Что проверить в табеле?  ")])

    text = await transcribe(llm, "muse-spark-1.2", ogg_opus(tone_wav(0.5)))

    assert text == "Что проверить в табеле?"
    request = llm.requests[0]
    audio = request["input"][0]["content"][1]["input_audio"]
    assert audio["format"] == "wav" and base64.b64decode(audio["data"])[:4] == b"RIFF"
    assert request["model"] == "muse-spark-1.2" and request["max_output_tokens"] >= 4000


async def test_empty_transcript_asks_to_repeat():
    with pytest.raises(VoiceError, match="повторите"):
        await transcribe(ScriptedLLM([turn(text=" ")]), "m", ogg_opus(tone_wav(0.5)))


@pytest.mark.parametrize(
    ("now_utc", "expected_hours"),
    [
        (datetime(2026, 10, 27, 2, 0, tzinfo=UTC), 1.5),  # 07:00 in Tashkent → 08:30 today
        (datetime(2026, 10, 27, 4, 0, tzinfo=UTC), 23.5),  # 09:00 → tomorrow 08:30
    ],
)
def test_brief_waits_until_the_next_local_time(now_utc, expected_hours):
    assert seconds_until(time(8, 30), now_utc) == expected_hours * 3600


def test_brief_time_setting():
    assert parse_brief_time("08:30") == time(8, 30)
    assert parse_brief_time("  ") is None
