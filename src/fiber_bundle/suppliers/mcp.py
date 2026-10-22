"""Supplier MCP definitions, sharing existing transport and connection identity."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    from ..agent_connections.http_client import Client

from .contracts import Compare, Condition, Evidence


def install(app: "FastMCP", client: "Client", annotations: "ToolAnnotations") -> None:
    @app.tool(annotations=annotations)
    def fiber_supplier_query(conditions: dict[str, Any]) -> dict[str, Any]:
        """Query supplier facts by vault/kind (offers, products, suppliers), exact model,
        category, text, currency, price_min/max with price_unit, dates, tax and specs.
        Specs use [{field, equals or minimum/maximum, unit}]. Unknowns do not match.
        Results include evidence, coverage, exact total and next_offset. Follow pagination
        with expected_snapshot; never treat this indexed subset as all vault knowledge.
        """
        client.context()
        data = Condition.model_validate(conditions)
        return dict(client.supplier("query", data.model_dump()))

    @app.tool(annotations=annotations)
    def fiber_compare_quotes(vault_id: str, quote_ids: list[str]) -> dict[str, Any]:
        """Compare explicit quote IDs only within identical product/spec/commercial bases.
        Missing tax, freight, dates or units never become free/zero/current assumptions.
        """
        client.context()
        data = Compare(vault_id=vault_id, quote_ids=quote_ids)
        return dict(client.supplier("compare", data.model_dump()))

    @app.tool(annotations=annotations)
    def fiber_supplier_evidence(
        vault_id: str, source_id: str, expected_sha256: str, offset: int = 0, length: int = 4000
    ) -> dict[str, Any]:
        """Read bounded source text with provenance and missing-original labels.
        This never delivers raw originals or upgrades historical facts to verified facts.
        """
        client.context()
        data = Evidence(
            vault_id=vault_id,
            source_id=source_id,
            expected_sha256=expected_sha256,
            offset=offset,
            length=length,
        )
        return dict(client.supplier("evidence", data.model_dump()))
