"""Stable contracts shared by workbook generation, parsing, validation and HTTP."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkbookField(Contract):
    key: str
    label: str
    field_type: str
    required: bool = False
    option_ids: list[str] = Field(default_factory=list)


class WorkbookManifest(Contract):
    contract_version: Literal[1] = 1
    product_schema_version: int = Field(ge=1)
    generated_at: datetime
    product_fields: list[WorkbookField]
    price_fields: list[WorkbookField]

