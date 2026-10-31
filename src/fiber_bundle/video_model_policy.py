"""Execution capacity for frame extraction, independent of optional Pi writing."""

from dataclasses import replace

from .execution_config import ExecutionConfig


def vision_policy(policy: ExecutionConfig) -> ExecutionConfig:
    if (
        getattr(policy, "provider", None) == "deepseek"
        and policy.model == "deepseek-flash"
        and policy.max_output_tokens < 16384
    ):
        # Reasoning and the final JSON share this allowance. The original Key
        # budget still reserves the entire capacity before each request.
        return replace(policy, max_output_tokens=16384)
    return policy
