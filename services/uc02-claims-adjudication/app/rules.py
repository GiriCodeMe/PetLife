"""
Rule-based adjudication engine for UC-02 Claims Adjudication.

Rules are applied in order R-01 through R-09 for each line item.
Returns a tuple of (decision, line_decisions, denial_reasons).

PHI RULE: This module MUST NOT log claim details, patient data, or financial amounts.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .models import InvoiceData, LineDecision, LineItem, PolicyRecord

logger = logging.getLogger(__name__)


def _parse_date(iso_str: str) -> date:
    """Parse an ISO date string to a date object."""
    return date.fromisoformat(iso_str)


async def adjudicate(
    invoice_data: dict,
    policy: PolicyRecord,
) -> tuple[str, list[LineDecision], list[str]]:
    """
    Apply rules R-01 through R-09 to every line item on the invoice.

    Args:
        invoice_data: Raw invoice dict (will be parsed as InvoiceData).
        policy: The policy record to adjudicate against.

    Returns:
        (decision, line_decisions, denial_reasons)
        decision is one of "APPROVED", "PARTIAL", "DENIED".
    """
    invoice = InvoiceData.model_validate(invoice_data)
    claim_date = _parse_date(invoice.date_of_service)
    policy_start = _parse_date(policy.policy_start)

    # Mutable running totals (do NOT log these — PHI rule)
    running_annual_used = policy.annual_benefit_used
    running_deductible_met = policy.deductible_met
    running_category_spent: dict[str, float] = dict(policy.category_spent)

    line_decisions: list[LineDecision] = []
    all_denial_reasons: list[str] = []
    has_approved = False
    has_partial = False
    has_denied = False

    for item in invoice.line_items:
        decision_entry = _adjudicate_line(
            item=item,
            policy=policy,
            claim_date=claim_date,
            policy_start=policy_start,
            running_annual_used=running_annual_used,
            running_deductible_met=running_deductible_met,
            running_category_spent=running_category_spent,
        )

        line_decisions.append(decision_entry)

        if decision_entry.denial_reason:
            if decision_entry.denial_reason not in all_denial_reasons:
                all_denial_reasons.append(decision_entry.denial_reason)

        # Track whether line was denied, partial, or fully approved
        if decision_entry.approved_amount <= 0.0:
            has_denied = True
        elif decision_entry.approved_amount < decision_entry.original_amount:
            has_partial = True
        else:
            has_approved = True

        # Update running totals for subsequent lines
        category = item.category.lower()
        running_category_spent[category] = (
            running_category_spent.get(category, 0.0) + decision_entry.eligible_amount
        )
        running_annual_used += decision_entry.approved_amount
        # Deductible credit: amount absorbed by deductible = eligible - approved / coinsurance
        if policy.coinsurance_pct > 0:
            deductible_absorbed = decision_entry.eligible_amount - (
                decision_entry.approved_amount / policy.coinsurance_pct
            )
        else:
            deductible_absorbed = 0.0
        if deductible_absorbed > 0:
            running_deductible_met = min(
                policy.deductible,
                running_deductible_met + deductible_absorbed,
            )

    # Determine overall decision
    total_approved = sum(ld.approved_amount for ld in line_decisions)
    total_original = sum(ld.original_amount for ld in line_decisions)

    if total_approved <= 0.0:
        overall_decision = "DENIED"
    elif total_approved < total_original:
        overall_decision = "PARTIAL"
    else:
        overall_decision = "APPROVED"

    return overall_decision, line_decisions, all_denial_reasons


def _adjudicate_line(
    item: LineItem,
    policy: PolicyRecord,
    claim_date: date,
    policy_start: date,
    running_annual_used: float,
    running_deductible_met: float,
    running_category_spent: dict[str, float],
) -> LineDecision:
    """
    Applies R-01..R-09 to a single line item.
    Returns a LineDecision with all applied rules and amounts.
    """
    applied_rules: list[str] = []
    denial_reason: str | None = None
    eligible_amount = item.amount
    approved_amount = 0.0
    category = item.category.lower()

    # ------------------------------------------------------------------
    # R-01: WAITING_PERIOD
    # ------------------------------------------------------------------
    waiting_days = policy.waiting_periods.get(category, 0)
    earliest_eligible = policy_start + timedelta(days=waiting_days)
    if claim_date < earliest_eligible:
        applied_rules.append("R-01_WAITING_PERIOD")
        denial_reason = "WAITING_PERIOD_NOT_MET"
        return LineDecision(
            line_description=item.description,
            original_amount=item.amount,
            eligible_amount=0.0,
            approved_amount=0.0,
            denial_reason=denial_reason,
            applied_rules=applied_rules,
        )

    # ------------------------------------------------------------------
    # R-02: EXCLUSION_CHECK
    # ------------------------------------------------------------------
    if item.procedure_code and item.procedure_code in policy.excluded_codes:
        applied_rules.append("R-02_EXCLUSION_CHECK")
        denial_reason = "EXCLUDED_CONDITION"
        return LineDecision(
            line_description=item.description,
            original_amount=item.amount,
            eligible_amount=0.0,
            approved_amount=0.0,
            denial_reason=denial_reason,
            applied_rules=applied_rules,
        )

    if item.diagnosis_code and item.diagnosis_code in policy.excluded_conditions:
        applied_rules.append("R-02_EXCLUSION_CHECK")
        denial_reason = "EXCLUDED_CONDITION"
        return LineDecision(
            line_description=item.description,
            original_amount=item.amount,
            eligible_amount=0.0,
            approved_amount=0.0,
            denial_reason=denial_reason,
            applied_rules=applied_rules,
        )

    # Also check description-level exclusion matches for known conditions
    for excl_cond in policy.excluded_conditions:
        if excl_cond.lower() in item.description.lower():
            applied_rules.append("R-02_EXCLUSION_CHECK")
            denial_reason = "EXCLUDED_CONDITION"
            return LineDecision(
                line_description=item.description,
                original_amount=item.amount,
                eligible_amount=0.0,
                approved_amount=0.0,
                denial_reason=denial_reason,
                applied_rules=applied_rules,
            )

    # ------------------------------------------------------------------
    # R-03: ANNUAL_BENEFIT_EXHAUSTED
    # ------------------------------------------------------------------
    annual_remaining = policy.annual_benefit_max - running_annual_used
    if annual_remaining <= 0.0:
        applied_rules.append("R-03_ANNUAL_BENEFIT_EXHAUSTED")
        denial_reason = "ANNUAL_BENEFIT_EXHAUSTED"
        return LineDecision(
            line_description=item.description,
            original_amount=item.amount,
            eligible_amount=0.0,
            approved_amount=0.0,
            denial_reason=denial_reason,
            applied_rules=applied_rules,
        )

    applied_rules.append("R-01_WAITING_PERIOD")
    applied_rules.append("R-02_EXCLUSION_CHECK")
    applied_rules.append("R-03_ANNUAL_BENEFIT_EXHAUSTED")

    # ------------------------------------------------------------------
    # R-04: CATEGORY_LIMIT
    # ------------------------------------------------------------------
    if category in policy.category_limits:
        category_limit = policy.category_limits[category]
        already_spent = running_category_spent.get(category, 0.0)
        remaining_category = category_limit - already_spent
        if remaining_category <= 0.0:
            applied_rules.append("R-04_CATEGORY_LIMIT")
            denial_reason = "CATEGORY_LIMIT_EXHAUSTED"
            return LineDecision(
                line_description=item.description,
                original_amount=item.amount,
                eligible_amount=0.0,
                approved_amount=0.0,
                denial_reason=denial_reason,
                applied_rules=applied_rules,
            )
        if eligible_amount > remaining_category:
            eligible_amount = remaining_category
            denial_reason = denial_reason or "CATEGORY_LIMIT_PARTIAL"
        applied_rules.append("R-04_CATEGORY_LIMIT")

    # ------------------------------------------------------------------
    # R-05: DEDUCTIBLE
    # ------------------------------------------------------------------
    deductible_remaining = max(0.0, policy.deductible - running_deductible_met)
    if deductible_remaining > 0.0:
        absorbed = min(eligible_amount, deductible_remaining)
        eligible_amount -= absorbed
        if eligible_amount <= 0.0:
            applied_rules.append("R-05_DEDUCTIBLE")
            denial_reason = denial_reason or "DEDUCTIBLE_NOT_MET"
            return LineDecision(
                line_description=item.description,
                original_amount=item.amount,
                eligible_amount=0.0,
                approved_amount=0.0,
                denial_reason=denial_reason,
                applied_rules=applied_rules,
            )
    applied_rules.append("R-05_DEDUCTIBLE")

    # ------------------------------------------------------------------
    # R-06: PARTIAL_CAP (per-incident max)
    # ------------------------------------------------------------------
    if policy.per_incident_max is not None and eligible_amount > policy.per_incident_max:
        eligible_amount = policy.per_incident_max
        denial_reason = denial_reason or "PER_INCIDENT_CAP_PARTIAL"
        applied_rules.append("R-06_PARTIAL_CAP")
    else:
        applied_rules.append("R-06_PARTIAL_CAP")

    # ------------------------------------------------------------------
    # R-07: ANNUAL_CAP (remaining annual benefit)
    # ------------------------------------------------------------------
    if eligible_amount > annual_remaining:
        eligible_amount = annual_remaining
        denial_reason = denial_reason or "ANNUAL_CAP_PARTIAL"
        applied_rules.append("R-07_ANNUAL_CAP")
    else:
        applied_rules.append("R-07_ANNUAL_CAP")

    # ------------------------------------------------------------------
    # R-08: COINSURANCE
    # ------------------------------------------------------------------
    approved_amount = eligible_amount * policy.coinsurance_pct
    applied_rules.append("R-08_COINSURANCE")

    # ------------------------------------------------------------------
    # R-09: APPROVE
    # ------------------------------------------------------------------
    applied_rules.append("R-09_APPROVE")

    # Clear partial denial reason if the full original amount is approved
    if approved_amount >= item.amount:
        denial_reason = None

    return LineDecision(
        line_description=item.description,
        original_amount=item.amount,
        eligible_amount=round(eligible_amount, 2),
        approved_amount=round(approved_amount, 2),
        denial_reason=denial_reason,
        applied_rules=applied_rules,
    )
