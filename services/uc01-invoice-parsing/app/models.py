from __future__ import annotations

import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALLOWED_CURRENCIES = {"USD", "GBP", "EUR", "CAD", "AUD"}
PROCEDURE_CODE_RE = re.compile(r"^[A-Z0-9\-]{3,15}$")


class LineItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str
    procedure_code: Optional[str] = None
    quantity: float
    unit_price: float
    amount: float

    @field_validator("procedure_code", mode="before")
    @classmethod
    def validate_procedure_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = str(v).strip()
        if v == "":
            return None
        if not PROCEDURE_CODE_RE.match(v):
            raise ValueError(
                f"procedure_code '{v}' does not match required pattern ^[A-Z0-9\\-]{{3,15}}$"
            )
        return v


class InvoiceData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    clinic_name: str
    clinic_address: Optional[str] = None
    clinic_phone: Optional[str] = None
    invoice_number: str
    invoice_date: str  # ISO date string e.g. "2024-03-15"
    visit_date: Optional[str] = None
    patient_name: str
    patient_species: str  # canine/feline/avian/other
    patient_breed: Optional[str] = None
    owner_name: str
    line_items: list[LineItem]
    subtotal: float
    tax_rate: Optional[float] = None
    tax_amount: float
    discount_amount: float
    total_due: float
    amount_paid: Optional[float] = None
    balance_due: Optional[float] = None
    currency: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)

    # RR-05: invoice_date must be in the past
    @field_validator("invoice_date", mode="before")
    @classmethod
    def validate_invoice_date_past(cls, v: str) -> str:
        v = str(v).strip()
        try:
            parsed = date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(
                f"invoice_date '{v}' is not a valid ISO date (YYYY-MM-DD)"
            ) from exc
        if parsed > date.today():
            raise ValueError(
                f"invoice_date '{v}' is in the future; RR-05 requires a past date"
            )
        return v

    # RR-06: currency must be a valid ISO-4217 code from the allowed set
    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = str(v).strip().upper()
        if v not in ALLOWED_CURRENCIES:
            raise ValueError(
                f"currency '{v}' is not in allowed set {ALLOWED_CURRENCIES} (RR-06)"
            )
        return v

    # RR-01: line_items must be non-empty
    @field_validator("line_items", mode="after")
    @classmethod
    def validate_line_items_nonempty(cls, v: list[LineItem]) -> list[LineItem]:
        if not v:
            raise ValueError("line_items must be a non-empty list (RR-01)")
        return v

    # RR-04: procedure_code on each line item already validated in LineItem,
    # but we re-check here to surface the rule reference clearly.
    # (LineItem validator already handles this.)

    @model_validator(mode="after")
    def validate_financial_consistency(self) -> "InvoiceData":
        errors: list[str] = []

        # RR-02: sum of line_items.amount must match subtotal within ±1%
        items_sum = sum(item.amount for item in self.line_items)
        if self.subtotal != 0:
            tolerance = abs(self.subtotal) * 0.01
        else:
            tolerance = 0.01
        if abs(items_sum - self.subtotal) > tolerance:
            errors.append(
                f"RR-02: sum of line_items amounts ({items_sum:.2f}) does not match "
                f"subtotal ({self.subtotal:.2f}) within ±1%"
            )

        # RR-03: total_due = subtotal + tax_amount - discount_amount within ±1%
        expected_total = self.subtotal + self.tax_amount - self.discount_amount
        if self.total_due != 0:
            tol = abs(self.total_due) * 0.01
        else:
            tol = 0.01
        if abs(self.total_due - expected_total) > tol:
            errors.append(
                f"RR-03: total_due ({self.total_due:.2f}) != "
                f"subtotal + tax_amount - discount_amount "
                f"({expected_total:.2f}) within ±1%"
            )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class ParseResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: str
    status: str  # "success" | "partial" | "failed"
    invoice: Optional[InvoiceData] = None
    validation_errors: list[str] = Field(default_factory=list)
    processing_time_ms: int
    model_used: str
