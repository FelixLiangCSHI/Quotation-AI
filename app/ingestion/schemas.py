"""Canonical schemas for offline SAP/Excel ingestion.

The ingestion pipeline is deliberately provider independent: it knows nothing
about SAP connectivity. It only knows how to turn a *workbook of unknown
shape* into typed canonical records.

Each dataset declares its canonical fields, which of them are required, their
value kinds, and the configurable aliases commonly seen in SAP or Excel
exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class FieldKind(str, Enum):
    """How a raw cell value is normalised."""

    TEXT = "text"
    IDENTIFIER = "identifier"
    DECIMAL = "decimal"
    INTEGER = "integer"
    DATE = "date"
    CURRENCY = "currency"
    REGION = "region"
    UNIT = "unit"


class DatasetKind(str, Enum):
    """A canonical dataset that a worksheet can be mapped onto."""

    PRODUCT_MASTER = "product_master"
    HISTORICAL_QUOTATION = "historical_quotation"
    PRICING = "pricing"
    COMPATIBILITY = "compatibility"
    COST_COMPONENT = "cost_component"


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: FieldKind
    required: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class CanonicalSchema:
    kind: DatasetKind
    title: str
    fields: tuple[CanonicalField, ...]
    #: Fields whose combination must be unique across the dataset.
    unique_key: tuple[str, ...] = ()
    #: Fields referencing ``ProductMasterRecord.product_id``.
    product_reference: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields if item.required)

    def field(self, name: str) -> CanonicalField:
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(name)


PRODUCT_MASTER_SCHEMA = CanonicalSchema(
    kind=DatasetKind.PRODUCT_MASTER,
    title="Product master",
    unique_key=("product_id",),
    fields=(
        CanonicalField(
            "product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("cat", "cat no", "material", "material number", "sku",
                     "product id", "product code", "matnr"),
        ),
        CanonicalField(
            "description",
            FieldKind.TEXT,
            required=True,
            aliases=("description", "material description", "product name",
                     "short text", "maktx"),
        ),
        CanonicalField(
            "product_family",
            FieldKind.TEXT,
            aliases=("prod family", "product family", "family"),
        ),
        CanonicalField(
            "product_type",
            FieldKind.TEXT,
            aliases=("prod type", "product type", "material type", "mtart"),
        ),
        CanonicalField(
            "unit_of_measure",
            FieldKind.UNIT,
            aliases=("uom", "unit", "base unit", "base unit of measure", "meins"),
        ),
        CanonicalField(
            "region",
            FieldKind.REGION,
            aliases=("region", "sales org region", "country", "market"),
        ),
        CanonicalField(
            "lifecycle_status",
            FieldKind.TEXT,
            aliases=("status", "lifecycle", "lifecycle status"),
        ),
    ),
)


HISTORICAL_QUOTATION_SCHEMA = CanonicalSchema(
    kind=DatasetKind.HISTORICAL_QUOTATION,
    title="Historical quotations",
    unique_key=("quotation_id", "product_id"),
    product_reference="product_id",
    fields=(
        CanonicalField(
            "quotation_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("quotation", "quotation id", "quote no", "quote number",
                     "sales document", "vbeln"),
        ),
        CanonicalField(
            "product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("cat", "material", "product id", "product code", "matnr"),
        ),
        CanonicalField(
            "customer_name",
            FieldKind.TEXT,
            aliases=("customer", "customer name", "sold to party", "kunnr"),
        ),
        CanonicalField(
            "quotation_date",
            FieldKind.DATE,
            required=True,
            aliases=("date", "quotation date", "quote date", "document date",
                     "audat"),
        ),
        CanonicalField(
            "quantity",
            FieldKind.INTEGER,
            required=True,
            aliases=("qty", "quantity", "order quantity", "kwmeng"),
        ),
        CanonicalField(
            "unit_of_measure",
            FieldKind.UNIT,
            aliases=("uom", "unit", "base unit", "meins"),
        ),
        CanonicalField(
            "net_price",
            FieldKind.DECIMAL,
            required=True,
            aliases=("net price", "net value", "quoted price", "netwr"),
        ),
        CanonicalField(
            "currency",
            FieldKind.CURRENCY,
            required=True,
            aliases=("currency", "curr", "doc currency", "waerk"),
        ),
        CanonicalField(
            "region",
            FieldKind.REGION,
            aliases=("region", "country", "sales org region"),
        ),
        CanonicalField(
            "outcome",
            FieldKind.TEXT,
            aliases=("outcome", "result", "won lost", "status"),
        ),
    ),
)


PRICING_SCHEMA = CanonicalSchema(
    kind=DatasetKind.PRICING,
    title="Pricing",
    unique_key=("product_id", "region", "currency"),
    product_reference="product_id",
    fields=(
        CanonicalField(
            "product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("cat", "material", "product id", "product code", "matnr"),
        ),
        CanonicalField(
            "description",
            FieldKind.TEXT,
            aliases=("description", "material description"),
        ),
        CanonicalField(
            "product_family",
            FieldKind.TEXT,
            aliases=("prod family", "product family"),
        ),
        CanonicalField(
            "product_type",
            FieldKind.TEXT,
            aliases=("prod type", "product type"),
        ),
        CanonicalField(
            "list_price",
            FieldKind.DECIMAL,
            required=True,
            aliases=("list price", "gross price", "price"),
        ),
        CanonicalField(
            "net_price",
            FieldKind.DECIMAL,
            required=True,
            aliases=("net price", "net"),
        ),
        CanonicalField(
            "minimum_price",
            FieldKind.DECIMAL,
            aliases=("minimum price", "min price", "floor price"),
        ),
        CanonicalField(
            "transfer_price",
            FieldKind.DECIMAL,
            aliases=("transfer price",),
        ),
        CanonicalField(
            "cogs",
            FieldKind.DECIMAL,
            aliases=("cogs", "cost of goods sold", "standard cost"),
        ),
        CanonicalField(
            "installation_cogs",
            FieldKind.DECIMAL,
            aliases=("installation cogs", "install cogs"),
        ),
        CanonicalField(
            "warranty_cogs",
            FieldKind.DECIMAL,
            aliases=("warranty cogs",),
        ),
        CanonicalField(
            "cogs_installation_warranty",
            FieldKind.DECIMAL,
            aliases=("cogs i w", "cogs i&w", "cogs incl installation warranty"),
        ),
        CanonicalField(
            "cogs_moving_average",
            FieldKind.DECIMAL,
            aliases=("cogs mov avg", "cogs moving average"),
        ),
        CanonicalField(
            "freight",
            FieldKind.DECIMAL,
            aliases=("freight",),
        ),
        CanonicalField(
            "duty",
            FieldKind.DECIMAL,
            aliases=("duty",),
        ),
        CanonicalField(
            "tariff",
            FieldKind.DECIMAL,
            aliases=("tariff",),
        ),
        CanonicalField(
            "service_net_price",
            FieldKind.DECIMAL,
            aliases=("service net price",),
        ),
        CanonicalField(
            "service_cogs",
            FieldKind.DECIMAL,
            aliases=("service cogs",),
        ),
        CanonicalField(
            "app_cogs",
            FieldKind.DECIMAL,
            aliases=("app cogs", "application cogs"),
        ),
        CanonicalField(
            "currency",
            FieldKind.CURRENCY,
            required=True,
            aliases=("currency", "curr", "waers"),
        ),
        CanonicalField(
            "region",
            FieldKind.REGION,
            aliases=("region", "country", "market"),
        ),
        CanonicalField(
            "unit_of_measure",
            FieldKind.UNIT,
            aliases=("uom", "unit", "price unit", "base unit"),
        ),
        CanonicalField(
            "valid_from",
            FieldKind.DATE,
            aliases=("valid from", "validity start", "datab"),
        ),
        CanonicalField(
            "valid_to",
            FieldKind.DATE,
            aliases=("valid to", "validity end", "datbi"),
        ),
    ),
    notes=(
        "net_price must not exceed list_price.",
        "net_price must not fall below minimum_price.",
        "cogs must not exceed net_price.",
    ),
)


COMPATIBILITY_SCHEMA = CanonicalSchema(
    kind=DatasetKind.COMPATIBILITY,
    title="Compatibility",
    unique_key=("product_id", "compatible_product_id"),
    product_reference="product_id",
    fields=(
        CanonicalField(
            "product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("cat", "material", "product id", "main product"),
        ),
        CanonicalField(
            "compatible_product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("compatible product", "compatible product id",
                     "option", "accessory", "component"),
        ),
        CanonicalField(
            "relation_type",
            FieldKind.TEXT,
            aliases=("relation", "relation type", "compatibility type"),
        ),
        CanonicalField(
            "region",
            FieldKind.REGION,
            aliases=("region", "country"),
        ),
        CanonicalField(
            "notes",
            FieldKind.TEXT,
            aliases=("notes", "comment", "remark"),
        ),
    ),
)


COST_COMPONENT_SCHEMA = CanonicalSchema(
    kind=DatasetKind.COST_COMPONENT,
    title="Cost components",
    unique_key=("product_id", "component_code", "currency"),
    product_reference="product_id",
    fields=(
        CanonicalField(
            "product_id",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("cat", "material", "product id"),
        ),
        CanonicalField(
            "component_code",
            FieldKind.IDENTIFIER,
            required=True,
            aliases=("component", "component code", "cost element",
                     "cost component"),
        ),
        CanonicalField(
            "component_name",
            FieldKind.TEXT,
            aliases=("component name", "cost component name", "description"),
        ),
        CanonicalField(
            "amount",
            FieldKind.DECIMAL,
            required=True,
            aliases=("amount", "cost", "value", "cost amount"),
        ),
        CanonicalField(
            "currency",
            FieldKind.CURRENCY,
            required=True,
            aliases=("currency", "curr", "waers"),
        ),
        CanonicalField(
            "unit_of_measure",
            FieldKind.UNIT,
            aliases=("uom", "unit", "base unit"),
        ),
        CanonicalField(
            "valid_from",
            FieldKind.DATE,
            aliases=("valid from", "validity start"),
        ),
    ),
)


CANONICAL_SCHEMAS: Mapping[DatasetKind, CanonicalSchema] = MappingProxyType(
    {
        schema.kind: schema
        for schema in (
            PRODUCT_MASTER_SCHEMA,
            HISTORICAL_QUOTATION_SCHEMA,
            PRICING_SCHEMA,
            COMPATIBILITY_SCHEMA,
            COST_COMPONENT_SCHEMA,
        )
    }
)


def get_schema(kind: DatasetKind | str) -> CanonicalSchema:
    dataset_kind = DatasetKind(kind)
    return CANONICAL_SCHEMAS[dataset_kind]


#: Currencies the MVP is allowed to ingest. Configurable through
#: ``INGESTION_SUPPORTED_CURRENCIES``; see :mod:`app.ingestion.config`.
DEFAULT_SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "CNY", "JPY", "AUD", "CAD")

#: Units treated as equivalent for consistency checks.
DEFAULT_UNIT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "ea": "EA",
        "each": "EA",
        "pc": "EA",
        "pce": "EA",
        "piece": "EA",
        "unit": "EA",
        "set": "SET",
        "kit": "SET",
        "hr": "HR",
        "hour": "HR",
        "h": "HR",
    }
)
