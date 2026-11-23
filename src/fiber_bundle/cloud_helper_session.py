"""One metadata-only helper per execution unit; every request queries fresh OS state."""

import json
import os
import select
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class Session:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.helper: Path | None = None

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        finally:
            if process.stdout:
                process.stdout.close()

    def query(
        self, helper: Path, mode: str, path: Path, names: tuple[str, ...], timeout: int
    ) -> bytes:
        payload = json.dumps({"mode": mode, "path": str(path), "names": names}).encode() + b"\n"
        if len(payload) > 262144:
            raise ValueError("cloud metadata request limit")
        if self.process is None:
            self.process = subprocess.Popen(
                [str(helper), "--serve-metadata"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            self.helper = helper
            assert self.process.stdin is not None and self.process.stdout is not None
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        process = self.process
        if self.helper != helper or process.poll() is not None:
            raise OSError("cloud metadata helper unavailable")
        assert process.stdin is not None and process.stdout is not None
        writer, reader = process.stdin.fileno(), process.stdout.fileno()
        deadline = time.monotonic() + timeout
        sent, result = 0, b""
        try:
            while b"\n" not in result:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(str(helper), timeout)
                readable, writable, _ = select.select(
                    [reader], [writer] if sent < len(payload) else [], [], remaining
                )
                if writable:
                    sent += os.write(writer, payload[sent : sent + 4096])
                if readable:
                    part = os.read(reader, 4097)
                    if not part:
                        raise OSError("cloud metadata helper closed")
                    result += part
                    if len(result) > 4096:
                        raise ValueError("cloud metadata response limit")
            if sent != len(payload) or not result.endswith(b"\n") or result.count(b"\n") != 1:
                raise ValueError("cloud metadata response framing")
            return result[:-1]
        except BaseException:
            self.close()
            raise


current: ContextVar[Session | None] = ContextVar("cloud_helper_session", default=None)


@contextmanager
def reuse() -> Iterator[None]:
    if current.get() is not None:
        yield
        return
    session = Session()
    token = current.set(session)
    try:
        yield
    finally:
        current.reset(token)
        session.close()


def query(
    helper: Path, mode: str, path: Path, *, names: tuple[str, ...] = (), timeout: int
) -> bytes | None:
    session = current.get()
    if session is None or mode not in {"--file", "--directory", "--items"}:
        return None
    return session.query(helper, mode, path, names, timeout)
