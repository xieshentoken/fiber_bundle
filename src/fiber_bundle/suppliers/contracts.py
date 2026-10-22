"""Typed supplier transport contracts, independent of storage and rendering."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from ..payloads import Identifier, Payload

Text = Annotated[str, Field(min_length=1, max_length=1000)]
Money = Annotated[str, Field(pattern=r"^(?:0|[1-9]\d{0,11})(?:\.\d{1,8})?$")]


class Scope(Payload):
    client_id: Identifier
    vault_id: Identifier


class ImportLegacy(Scope):
    request_id: Identifier
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Policy(Payload):
    enabled: bool = False
    cloud_allowed: bool = False
    review_mode: Literal["manual", "automatic"] = "manual"
    model_configuration_id: Identifier = "app-default"


class SavePolicy(Scope):
    expected_revision: Annotated[int, Field(ge=0)]
    settings: Policy


class Specification(Payload):
    field: Annotated[str, Field(min_length=1, max_length=100)]
    equals: Annotated[str, Field(max_length=300)] | None = None
    minimum: Money | None = None
    maximum: Money | None = None
    unit: Annotated[str, Field(max_length=30)] = ""

    @model_validator(mode="after")
    def bounds(self) -> "Specification":
        from decimal import Decimal

        if self.equals is None and self.minimum is None and self.maximum is None:
            raise ValueError("supplier_spec_condition_required")
        if self.minimum and self.maximum and Decimal(self.minimum) > Decimal(self.maximum):
            raise ValueError("supplier_range_invalid")
        return self


class Condition(Payload):
    vault_id: Identifier
    supplier_id: Identifier | None = None
    kind: Literal["offers", "products", "suppliers"] = "offers"
    expected_snapshot: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    specs: Annotated[list[Specification], Field(max_length=20)] = []
    text: Annotated[str, Field(max_length=300)] = ""
    category: Annotated[str, Field(max_length=100)] = ""
    model: Annotated[str, Field(max_length=200)] = ""
    currency: Literal["CNY", "USD", "EUR", "JPY", "GBP", "HKD"] | None = None
    price_min: Money | None = None
    price_max: Money | None = None
    price_unit: Literal["kg", "g", "100g", "t", "piece", "set", "m", "m2", "L"] | None = None
    quoted_after: str | None = None
    quoted_before: str | None = None
    valid_on: str | None = None
    tax: Literal["included", "excluded"] | None = None
    include_legacy: bool = True
    include_unresolved: bool = False
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 25

    @model_validator(mode="after")
    def bounds(self) -> "Condition":
        from decimal import Decimal

        if self.price_min is not None or self.price_max is not None:
            if self.currency is None or self.price_unit is None:
                raise ValueError("supplier_price_basis_required")
        if (
            self.price_min is not None
            and self.price_max is not None
            and Decimal(self.price_min) > Decimal(self.price_max)
        ):
            raise ValueError("supplier_range_invalid")
        if self.quoted_after and self.quoted_before and self.quoted_after > self.quoted_before:
            raise ValueError("supplier_range_invalid")
        return self

    @field_validator("quoted_after", "quoted_before", "valid_on")
    @classmethod
    def date(cls, value: str | None) -> str | None:
        if value is not None:
            from datetime import date

            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("supplier_date_invalid")
        return value


class OwnedCondition(Condition):
    client_id: Identifier


class Offer(Payload):
    supplier_name: Text
    product_name: Text
    model: Annotated[str, Field(max_length=200)] = ""
    category: Annotated[str, Field(max_length=100)] = ""
    amount: Money | None = None
    currency: Literal["CNY", "USD", "EUR", "JPY", "GBP", "HKD"] | None = None
    unit: Literal["kg", "g", "100g", "t", "piece", "set", "m", "m2", "L"] | None = None
    quoted_at: str | None = None
    valid_until: str | None = None
    tax: Literal["included", "excluded", "unknown"] = "unknown"
    freight: Literal["included", "excluded", "unknown"] = "unknown"
    minimum_quantity: Money | None = None
    maximum_quantity: Money | None = None
    quantity_unit: Annotated[str, Field(max_length=50)] = ""
    lead_time: Annotated[str, Field(max_length=200)] = ""
    payment: Annotated[str, Field(max_length=500)] = ""
    specs: Annotated[dict[str, str], Field(max_length=30)] = {}
    contact_name: Annotated[str, Field(max_length=100)] = ""
    phone: Annotated[str, Field(max_length=100)] = ""
    company_evidence: Annotated[str, Field(max_length=2000)] = ""
    evidence_quote: Annotated[str, Field(min_length=1, max_length=20000)]

    @field_validator("quoted_at", "valid_until")
    @classmethod
    def date(cls, value: str | None) -> str | None:
        return Condition.date(value)


class Extraction(Payload):
    offers: Annotated[list[Offer], Field(max_length=1000)]
    warnings: Annotated[list[str], Field(max_length=100)] = []


class ManualQuote(Scope):
    request_id: Identifier
    supplier_id: Identifier | None = None
    offer: Offer


class DocumentImport(Scope):
    run_id: Identifier
    supplier_id: Identifier | None = None


class JobAction(Scope):
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Correction(Scope):
    request_id: Identifier
    kind: Literal["supplier", "product", "quote"]
    identity: Identifier
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    fields: Annotated[dict[str, str], Field(min_length=1, max_length=30)]
    reason: Text


class Compare(Payload):
    vault_id: Identifier
    quote_ids: Annotated[list[Identifier], Field(min_length=2, max_length=100)]


class OwnedCompare(Compare):
    client_id: Identifier


class Evidence(Payload):
    vault_id: Identifier
    source_id: Identifier
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    offset: Annotated[int, Field(ge=0, le=10000000)] = 0
    length: Annotated[int, Field(ge=1, le=8000)] = 4000
