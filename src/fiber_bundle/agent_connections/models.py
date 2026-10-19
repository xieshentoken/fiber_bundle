"""Explicit local/OpenCodeGo references; projections never read credentials or enable routes."""

from dataclasses import asdict
from typing import Any

from ..config import Config, InvalidInput
from ..query_policy_store import legacy_current as current


def binding_digest(config: Config, reference: str | None) -> str | None:
    if reference == "app-default" or (reference and reference.startswith("model_")):
        import hashlib

        return hashlib.sha256(reference.encode()).hexdigest()
    route = current(config)
    return route.digest if route else None


def local(config: Config, reference: str | None) -> dict[str, Any]:
    route = current(config)
    if reference != "local-primary" or route is None or route.local_query is None:
        raise InvalidInput("agent_local_model_unconfigured")
    route.validate()
    return {"configuration_sha256": route.digest, **asdict(route.local_query)}


def resolve(config: Config, reference: str | None) -> dict[str, Any]:
    if reference == "app-default" or (reference and reference.startswith("model_")):
        from ..model_configuration.policy import snapshot

        return snapshot(config, reference)
    if reference == "local-primary":
        return {**local(config, reference), "processing_location": "local"}
    route = current(config)
    if reference != "opencodego-primary" or route is None or route.cloud_answer is None:
        raise InvalidInput("agent_model_unconfigured")
    route.validate()
    return {
        "configuration_sha256": route.digest,
        "processing_location": "cloud",
        "provider": "opencodego",
        **asdict(route.cloud_answer),
    }


def available(config: Config) -> list[dict[str, Any]]:
    from .. import database
    from ..model_configuration import store as catalog

    with database.connect(config.state_root) as db:
        saved = catalog.inspect(db)
    registered = []
    keys = {k["id"]: k for k in saved["credentials"]}
    for item in saved["models"]:
        if item["active"]:
            key = keys[item["credential_id"]]
            registered.append(
                {
                    "id": item["id"],
                    "name": key["name"] + " · " + item["model"],
                    "endpoint": key["base_url"],
                    "provider": key["provider"],
                    "processing_location": "local" if key["provider"] == "local" else "cloud",
                    "configuration_sha256": None,
                    "effort": item["effort"],
                }
            )
    if saved["default"]["configuration_id"]:
        registered.insert(
            0,
            {
                "id": "app-default",
                "name": "跟随应用默认模型",
                "processing_location": "managed",
                "endpoint": "",
                "configuration_sha256": None,
            },
        )
    route = current(config)
    if route is None:
        return registered
    route.validate()
    result = []
    for reference, value, location in (
        ("local-primary", route.local_query, "local"),
        ("opencodego-primary", route.cloud_answer, "cloud"),
    ):
        if value is None:
            continue
        item = {
            "id": reference,
            "name": value.model,
            "endpoint": value.endpoint,
            "processing_location": location,
            "configuration_sha256": route.digest,
            "daily_calls": value.daily_calls,
            "max_output_tokens": value.max_output_tokens,
        }
        if location == "cloud":
            assert route.cloud_answer is not None
            item["provider"] = "opencodego"
            item["daily_limit_nusd"] = route.cloud_answer.daily_limit_nusd
            item["currency"] = "USD"
        result.append(item)
    return registered + result
