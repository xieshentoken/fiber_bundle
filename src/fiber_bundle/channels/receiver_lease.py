"""Exactly one platform receiver per bot account, including legacy mixed entries."""

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import Config, InvalidInput, make_directory
from . import store


@contextmanager
def owned(config: Config, channel: dict[str, Any], action: str) -> Iterator[None]:
    value = channel["value"]
    account = value.get("app_id")
    if value["platform"] == "weixin":
        path = store.home(config, channel["id"]) / "weixin-login.json"
        if path.exists():
            account = json.loads(path.read_text()).get("account_id")
        if not account and action == "run":
            raise InvalidInput("channel_weixin_login_required")
    if not account:
        yield
        return
    root = config.project_root / ".fiber-services/accounts"
    make_directory(root)
    key = hashlib.sha256((value["platform"] + ":" + account).encode()).hexdigest()
    fd = os.open(root / (key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("channel_account_already_running") from None
        yield
    finally:
        os.close(fd)
