"""UTC daily projections of recorded cloud calls; estimates are never provider invoices."""

import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import query_policy_store
from .config import Config, InvalidInput
from .execution_config import ExecutionConfig
from .usage_credentials import reference

# A missing usage response retains its reservation, even when an Agent stored a ceiling as cost.
CALLS = """
WITH calls AS (
 SELECT c.created_at,c.model,c.state,c.reserved_nusd,c.charged_nusd,
   c.input_tokens,c.output_tokens,coalesce(k.credential_id,'unattributed') AS key_id,
   coalesce(k.label,'历史未归属') AS label,coalesce(k.provider,'unknown') AS provider
 FROM model_calls c LEFT JOIN model_call_credentials k ON k.call_id=c.id
 UNION ALL
 SELECT o.created_at,json_extract(o.payload_json,'$.cloud_reservation.model'),
   CASE WHEN o.state='succeeded'
     AND json_extract(o.result_json,'$.usage_state')='reported'
     AND json_type(o.result_json,'$.cloud_cost_nusd')='integer'
     AND json_extract(o.result_json,'$.cloud_cost_nusd')>=0 THEN 'settled'
     WHEN o.state IN ('queued','running') THEN 'reserved' ELSE 'uncertain' END,
   json_extract(o.payload_json,'$.cloud_reservation.reserved_nusd'),
   json_extract(o.result_json,'$.cloud_cost_nusd'),
   json_extract(o.result_json,'$.usage.prompt_tokens'),
   json_extract(o.result_json,'$.usage.completion_tokens'),
   coalesce((SELECT credential_id FROM model_credential_aliases a WHERE a.original_id=
     json_extract(o.payload_json,'$.cloud_reservation.credential_id')),
     json_extract(o.payload_json,'$.cloud_reservation.credential_id'),'unattributed'),
   coalesce(json_extract(o.payload_json,'$.cloud_reservation.credential_label'),'历史未归属'),
   coalesce(json_extract(o.payload_json,'$.cloud_reservation.provider'),'opencodego')
 FROM agent_connection_operations o WHERE o.kind='test_query'
   AND json_type(o.payload_json,'$.cloud_reservation')='object'
 UNION ALL
 SELECT c.created_at,c.model,
   CASE WHEN c.state='succeeded' AND c.input_tokens IS NOT NULL AND c.output_tokens IS NOT NULL
     THEN 'settled'
     WHEN c.state IN ('queued','running') THEN 'reserved' ELSE 'uncertain' END,
   0,c.charged_nusd,c.input_tokens,c.output_tokens,c.credential_id,
   json_extract(k.settings_json,'$.name'),c.provider
 FROM model_configuration_calls c JOIN model_credentials k ON k.id=c.credential_id
)
"""

TOTALS = """count(*) AS calls,
          sum(CASE WHEN state='settled' AND charged_nusd IS NOT NULL THEN 1 ELSE 0 END)
            AS settled_calls,
          sum(CASE WHEN state='settled' AND charged_nusd IS NOT NULL THEN 0 ELSE 1 END)
            AS unsettled_calls,
          sum(CASE WHEN state='settled' AND charged_nusd IS NULL THEN 1 ELSE 0 END)
            AS price_unknown_calls,
          sum(CASE WHEN state='reserved' THEN 1 ELSE 0 END) AS pending_calls,
          sum(CASE WHEN state NOT IN ('settled','reserved') THEN 1 ELSE 0 END)
            AS result_unknown_calls,
          sum(CASE WHEN state='settled' THEN coalesce(charged_nusd,0) ELSE 0 END)
            AS estimated_nusd,
          sum(CASE WHEN state!='settled' OR charged_nusd IS NULL THEN reserved_nusd ELSE 0 END)
            AS reserved_nusd,
          sum(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END)
            AS token_unknown_calls,
          sum(coalesce(input_tokens,0)) AS input_tokens,
          sum(coalesce(output_tokens,0)) AS output_tokens"""
METRICS = (
    "calls",
    "settled_calls",
    "unsettled_calls",
    "price_unknown_calls",
    "pending_calls",
    "result_unknown_calls",
    "estimated_nusd",
    "reserved_nusd",
    "input_tokens",
    "output_tokens",
    "token_unknown_calls",
)


def bounds(start: str | None, end: str | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    try:
        last = date.fromisoformat(end) if end else today
        first = date.fromisoformat(start) if start else last - timedelta(days=29)
    except (TypeError, ValueError):
        raise InvalidInput("usage_date_invalid") from None
    if (start and first.isoformat() != start) or (end and last.isoformat() != end):
        raise InvalidInput("usage_date_invalid")
    if not 0 <= (last - first).days <= 365:
        raise InvalidInput("usage_range_invalid")
    return first, last


def configured(
    config: Config, execution: ExecutionConfig | None, db: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    policies = []
    if execution is not None:
        policies.append((execution, "执行模型"))
    route = query_policy_store.current(config)
    if route is not None and route.cloud_answer is not None:
        policies.append((route.cloud_answer, "云端回答 / Agent"))
    result: dict[str, dict[str, Any]] = {}
    for policy, purpose in policies:
        key = reference(policy.secret_file, db)
        item = result.setdefault(
            key["id"], {**key, "configured": True, "models": [], "purposes": []}
        )
        if policy.model not in item["models"]:
            item["models"].append(policy.model)
        item["purposes"].append(purpose)
    if db is not None:
        from .model_configuration import store as models

        catalog = models.inspect(db)
        for credential in catalog["credentials"]:
            selected = [
                m["model"] for m in catalog["models"] if m["credential_id"] == credential["id"]
            ]
            result[credential["id"]] = {
                "id": credential["id"],
                "label": credential["name"],
                "provider": credential["provider"],
                "configured": credential["active"],
                "models": selected,
                "purposes": ["模型配置"],
            }
    return list(result.values())


def report(
    config: Config,
    db: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    api_key_id: str = "all",
    execution: ExecutionConfig | None = None,
) -> dict[str, Any]:
    first, last = bounds(start, end)
    if api_key_id not in {"all", "unattributed"} and not re.fullmatch(
        r"key_[0-9a-f]{64}", api_key_id
    ):
        raise InvalidInput("usage_key_invalid")
    keys = {key["id"]: key for key in configured(config, execution, db)}
    historical = db.execute(
        CALLS + "SELECT key_id,max(label) AS label,max(provider) AS provider "
        "FROM calls GROUP BY key_id ORDER BY key_id LIMIT 201"
    ).fetchall()
    if len(historical) > 200:
        raise InvalidInput("usage_key_limit")
    for row in historical:
        keys.setdefault(
            row["key_id"],
            {
                "id": row["key_id"],
                "label": row["label"],
                "provider": row["provider"],
                "configured": False,
                "models": [],
                "purposes": [],
            },
        )
    if api_key_id != "all" and api_key_id not in keys:
        raise InvalidInput("usage_key_unknown")
    begin = datetime.combine(first, datetime.min.time(), UTC).timestamp()
    finish = datetime.combine(last + timedelta(days=1), datetime.min.time(), UTC).timestamp()
    # Group in SQLite, rather than imposing a row cap that could silently undercount costs.
    rows = db.execute(
        CALLS
        + f"""SELECT date(created_at,'unixepoch') AS day,key_id,model,{TOTALS}
        FROM calls WHERE created_at>=? AND created_at<? AND (?='all' OR key_id=?)
        GROUP BY day,key_id,model ORDER BY day,key_id,model LIMIT 5001""",
        (begin, finish, api_key_id, api_key_id),
    ).fetchall()
    if len(rows) > 5000:
        raise InvalidInput("usage_group_limit; narrow date range")

    def empty() -> dict[str, int]:
        return dict.fromkeys(METRICS, 0)

    daily = {
        (first + timedelta(days=offset)).isoformat(): empty()
        for offset in range((last - first).days + 1)
    }
    summary = empty()
    by_key = {
        key["id"]: {**key, **empty(), "models": list(key["models"])}
        for key in keys.values()
        if api_key_id in {"all", key["id"]}
    }
    for row in rows:
        item = by_key[row["key_id"]]
        if row["model"] and row["model"] not in item["models"]:
            item["models"].append(row["model"])
        for metric in METRICS:
            amount = row[metric] or 0
            if type(amount) is not int or amount < 0:
                raise InvalidInput("usage_ledger_invalid")
            summary[metric] += amount
            daily[row["day"]][metric] += amount
            item[metric] += amount
    lifetime_row = db.execute(
        CALLS + f"SELECT {TOTALS} FROM calls WHERE (?='all' OR key_id=?)",
        (api_key_id, api_key_id),
    ).fetchone()
    lifetime = {metric: lifetime_row[metric] or 0 for metric in METRICS}
    if any(type(amount) is not int or amount < 0 for amount in lifetime.values()):
        raise InvalidInput("usage_ledger_invalid")
    local_calls = db.execute(
        "SELECT count(*) FROM local_query_calls WHERE created_at>=? AND created_at<?",
        (begin, finish),
    ).fetchone()[0]
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "timezone": "UTC",
        "currency": "USD",
        "cost_basis": "configured_price_estimate",
        "count_basis": "recorded_reservations",
        "credential_basis": "credential_file_reference",
        "api_key_id": api_key_id,
        "keys": list(keys.values()),
        "summary": summary,
        "lifetime": lifetime,
        "daily": [{"day": day, **value} for day, value in daily.items()],
        "by_key": sorted(
            by_key.values(), key=lambda item: (-item["estimated_nusd"], item["label"])
        ),
        "local_calls": local_calls,
    }
