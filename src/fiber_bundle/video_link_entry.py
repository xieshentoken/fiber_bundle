"""Public-only downloader entry using the existing fixed acquisition implementation."""

from pathlib import Path

from .media_link_capture import capture
from .media_link_failure import AcquisitionBlocked


def main() -> int:
    try:
        capture(Path("/task/input"), Path("/task/output"), long_video=True)
        return 0
    except AcquisitionBlocked as error:
        print(error.reason)
        return 1
    except Exception:
        print("acquisition_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
