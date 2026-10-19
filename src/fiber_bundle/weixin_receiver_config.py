"""Explicit receiver startup limits, separate from execution/publication worker settings."""

from dataclasses import dataclass
from pathlib import Path

from . import (
    weixin_batches,
    weixin_media_dispatch,
    weixin_outbound_dispatch,
    weixin_store_dispatch,
    weixin_task_submission,
)
from .article_worker import CapturePolicy
from .config import Config, InvalidInput, contained
from .weixin_events import Binding
from .weixin_worker_config import read_json


@dataclass(frozen=True)
class ReceiverConfig:
    binding: Binding
    enabled: bool = False
    version: int = 1
    quiet_seconds: float = 3
    intent_routing: bool = False
    media_limit: int = 0
    store_limit: int = 0
    submission_limit: int = 0
    outbound_limit: int = 0
    result_receipts: bool = False
    execution_config: str = "configs/execution.example.json"
    capture_policy: CapturePolicy | None = None

    def validate(self, config: Config) -> None:
        config.validate()
        if type(self.version) is not int or self.version != 1:
            raise InvalidInput("unsupported receiver startup version")
        if not isinstance(self.binding, Binding):
            raise InvalidInput("explicit receiver binding required")
        self.binding.validate()
        if any(
            type(v) is not bool for v in (self.enabled, self.intent_routing, self.result_receipts)
        ):
            raise InvalidInput("receiver switches must be boolean")
        weixin_batches.seconds(self.quiet_seconds)
        weixin_media_dispatch.limit(self.media_limit)
        weixin_store_dispatch.limit(self.store_limit)
        weixin_task_submission.limit(self.submission_limit)
        weixin_outbound_dispatch.limit(self.outbound_limit)
        if self.result_receipts and not self.outbound_limit:
            raise InvalidInput("result receipts require outbound budget")
        contained(config.project_root, self.execution_config)
        if self.capture_policy is not None:
            if not isinstance(self.capture_policy, CapturePolicy):
                raise InvalidInput("invalid receiver capture policy")
            if not isinstance(self.capture_policy.image, str):
                raise InvalidInput("invalid receiver capture image")
            self.capture_policy.validate()


def load_receiver_config(config: Config, path: Path) -> ReceiverConfig:
    data = read_json(config, path)
    if set(data) != set(ReceiverConfig.__dataclass_fields__):
        raise InvalidInput("invalid receiver configuration fields")
    try:
        if not isinstance(data["binding"], dict):
            raise TypeError
        capture = data["capture_policy"]
        if capture is not None and not isinstance(capture, dict):
            raise TypeError
        result = ReceiverConfig(
            **{
                **data,
                "binding": Binding(**data["binding"]),
                "capture_policy": CapturePolicy(**capture) if capture is not None else None,
            }
        )
    except TypeError:
        raise InvalidInput("invalid receiver binding or capture fields") from None
    result.validate(config)
    return result
