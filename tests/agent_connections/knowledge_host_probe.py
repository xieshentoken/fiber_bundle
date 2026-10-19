"""Installed Hermes Skill and MCP framework probe; isolated profile, no model."""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
checkout, root = (Path(p) for p in sys.argv[1:])
sys.path.insert(0, str(checkout))

from tools.mcp_tool import discover_mcp_tools, shutdown_mcp_servers  # noqa: E402
from tools.registry import registry  # noqa: E402
from tools.skills_tool import skill_view, skills_list  # noqa: E402

assert "fiber-bundle-access" in skills_list()
assert "knowledge_verified=false" in skill_view("fiber-bundle-access", preprocess=False)


def call(names, suffix, args):
    name = next(n for n in names if n.rsplit("__", 1)[-1] == suffix)
    result = registry.dispatch(name, args)
    result = json.loads(result) if isinstance(result, str) else result
    return result.get("structuredContent", result)


try:
    names = discover_mcp_tools()
    assert len([n for n in names if n.rsplit("__", 1)[-1].startswith("fiber_")]) == 6
    packet = call(names, "fiber_knowledge_catalog", {"limit": 50})
    expert = next(m for m in packet["items"] if m["kind"] == "expert")
    result = call(
        names,
        "fiber_query_expert",
        {
            "module_id": expert["module_id"],
            "expected_version": expert["version"],
            "question": "Synthetic",
            "limit": 5,
        },
    )
    assert result["query_result"]["evidence_ids"] and not result["knowledge_verified"]
    objects = call(
        names,
        "fiber_read_evidence",
        {"module_id": expert["module_id"], "expected_version": expert["version"], "kind": "module"},
    )
    obj = objects["objects"][0]
    anchor = call(
        names,
        "fiber_read_evidence",
        {
            "module_id": expert["module_id"],
            "expected_version": expert["version"],
            "kind": "anchor",
            "identity": obj["evidence_ids"][0],
        },
    )
    assert anchor["review_state"] == "deferred" and not anchor["knowledge_verified"]
    video = next(m for m in packet["items"] if m.get("source_kind") == "published_media")
    evidence = call(
        names,
        "fiber_read_evidence",
        {
            "module_id": video["module_id"],
            "expected_version": video["version"],
            "time_start_ms": 60000,
            "time_end_ms": 70000,
            "end_line": 200,
        },
    )
    assert "[60000–70000 ms]" in evidence["text"]
    search = call(names, "fiber_search", {"query": "Synthetic", "limit": 5})
    assert search["results"]
    (root / "host-result.json").write_text(
        json.dumps(
            {
                "state": "passed",
                "skill_loaded": True,
                "model_called": False,
                "expert_assurance": result["review_state"],
                "video_time_read": True,
                "citations": [
                    {
                        "module_id": expert["module_id"],
                        "version": expert["version"],
                        "object_id": obj["id"],
                        "evidence_id": obj["evidence_ids"][0],
                    },
                    {
                        "module_id": video["module_id"],
                        "version": video["version"],
                        "lines": evidence["lines"],
                        "time_ms": [60000, 70000],
                    },
                ],
            },
            indent=2,
        )
    )
finally:
    shutdown_mcp_servers()
