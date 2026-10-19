"""Small immutable binding shared by import plans, Pi tasks and compiler workers."""

import re
from typing import Any

from . import expert_runtime
from .config import InvalidInput

SEARCH_RUNTIME_SHA256 = "90a264aed1503bef1c8b80c41ec3069c4be32d3f9c207d4bf583eae7aff0573a"


def validate(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not {"schema", "id", "snapshot_sha256", "compiler_sha256", "pi_sha256", "runtime"}
        <= set(value)
        <= {
            "schema",
            "id",
            "snapshot_sha256",
            "compiler_sha256",
            "pi_sha256",
            "runtime",
            "features",
            "host_tools_sha256",
        }
        or value["schema"] != "fiber-project-skill/v1"
        or value["id"] != "fake-expert"
        or any(
            not isinstance(value[k], str) or re.fullmatch(r"[a-f0-9]{64}", value[k]) is None
            for k in ("snapshot_sha256", "compiler_sha256", "pi_sha256")
        )
    ):
        raise InvalidInput("project_skill_binding_invalid")
    if "host_tools_sha256" in value and (
        not isinstance(value["host_tools_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", value["host_tools_sha256"]) is None
    ):
        raise InvalidInput("project_skill_binding_invalid")
    if "features" in value:
        features = value["features"]
        if (
            not isinstance(features, dict)
            or set(features) != {"direct_compiler", "search_runtime", "figures"}
            or any(not isinstance(features[key], str) for key in features)
            or features["figures"] not in {"none", "index", "index+images"}
            or (features["direct_compiler"], features["search_runtime"])
            not in {
                ("1.1.0-direct-reference", "portable-reference-v0.1"),
                ("1.4.0-direct-reference", "reference-search-bm25-v0.3"),
            }
        ):
            raise InvalidInput("project_skill_binding_invalid")
    expert_runtime.verify(value["runtime"])
    if value["runtime"]["skill_sha256"] != value["compiler_sha256"]:
        raise InvalidInput("project_skill_binding_invalid")
    return value


def figure_mode(binding: dict[str, Any] | None, profile: str) -> str | None:
    features = (binding or {}).get("features", {})
    if features.get("direct_compiler") != "1.4.0-direct-reference":
        return None  # Old CLIs have no --figures option.
    return str(features["figures"]) if profile == "native-text" else "none"
