"""Bounded multipart audio transcription using existing protected model credentials."""

import io
import json
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import InvalidInput
from .execution_config import read_secret
from .model_configuration.discovery import NoRedirect, guard_address, headers, http_error
from .model_configuration.errors import ModelError


def endpoint(policy: Any) -> str:
    if policy.provider not in {"openai", "custom", "local"}:
        raise InvalidInput("video_asr_api_protocol_unsupported")
    for suffix in ("/chat/completions", "/responses"):
        if policy.endpoint.endswith(suffix):
            return str(policy.endpoint[: -len(suffix)]) + "/audio/transcriptions"
    raise InvalidInput("video_asr_api_protocol_unsupported")


def audio_chunk(path: Path, start: int, count: int) -> bytes:
    with wave.open(str(path)) as src:
        if src.getnchannels() != 1 or src.getsampwidth() != 2 or src.getframerate() != 16000:
            raise InvalidInput("video_asr_audio_invalid")
        src.setpos(start)
        raw = src.readframes(count)
        result = io.BytesIO()
        with wave.open(result, "wb") as out:
            out.setparams(src.getparams())
            out.writeframes(raw)
    return result.getvalue()


def transcribe(
    policy: Any, audio: bytes, prompt: str, language: str, format: str
) -> dict[str, Any]:
    url = endpoint(policy)
    guard_address(url, policy.provider == "local")
    boundary = "fiber" + uuid4().hex
    fields = {"model": policy.model, "response_format": format, "prompt": prompt[:2000]}
    if language != "auto":
        fields["language"] = language
    body = bytearray()
    for name, value in fields.items():
        body.extend(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    body.extend(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file";'
            ' filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
    )
    body.extend(audio)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    if len(body) > 2 * 1024**2:
        raise InvalidInput("video_asr_audio_invalid")
    key = read_secret(Path(policy.secret_file))
    request_headers = headers(policy.provider, key)
    request_headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    request = urllib.request.Request(url, data=bytes(body), headers=request_headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=policy.timeout_seconds) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ModelError("model_response_invalid")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("text"), str)
            or len(value["text"]) > 100000
        ):
            raise ModelError("model_response_invalid")
        return {"text": value["text"].strip(), "usage": value.get("usage")}
    except urllib.error.HTTPError as error:
        raise http_error(error) from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise ModelError("model_transport_failure") from None
    except (ValueError, UnicodeError):
        raise ModelError("model_response_invalid") from None


def tokens(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    incoming, outgoing = value.get("input_tokens"), value.get("output_tokens")
    if type(incoming) is int and incoming >= 0 and type(outgoing) is int and outgoing >= 0:
        return incoming, outgoing
    return None
