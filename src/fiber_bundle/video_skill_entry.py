"""Fixed public metadata probe for V3, executed without application credentials."""

import sys
from pathlib import Path


def main() -> None:
    skill, job = (Path(arg) for arg in sys.argv[1:])
    sys.path.insert(0, str(skill / "scripts"))
    from acquire_public import acquire_metadata  # type: ignore[import-not-found]
    from common import read_json, utc_now, write_json  # type: ignore[import-not-found]

    source = read_json(job / "metadata/job.json")["source"]
    metadata = acquire_metadata(job, source["url"])
    if metadata.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        raise ValueError("public source unavailable")
    access = read_json(job / "metadata/access.json")
    access.update(
        status="public",
        access_state="public_verified",
        verified_by="fiber-public-metadata-probe",
        verified_at=utc_now(),
    )
    write_json(job / "metadata/access.json", access)


if __name__ == "__main__":
    main()
