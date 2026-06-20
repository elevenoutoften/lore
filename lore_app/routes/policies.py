from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_ledger_db
from ..schemas import PolicyRule
from .api_keys import require_lore_key_admin

if TYPE_CHECKING:
    from ..ledger import LedgerDB

router = APIRouter(prefix="/api/policies", tags=["policies"])


@router.get("", response_model=list[PolicyRule])
def list_policies(
    gate: str | None = Query(default=None),
    enabled_only: bool = Query(default=True),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> list[PolicyRule]:
    return ledger.list_policies(gate=gate, enabled_only=enabled_only)


@router.get("/{policy_id}", response_model=PolicyRule)
def get_policy(policy_id: str, ledger: LedgerDB = Depends(get_ledger_db)) -> PolicyRule:
    policy = ledger.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return policy


@router.post("", response_model=PolicyRule)
def store_policy(
    policy: PolicyRule,
    _admin: None = Depends(require_lore_key_admin),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> PolicyRule:
    return ledger.store_policy(policy)


@router.delete("/{policy_id}")
def delete_policy(
    policy_id: str,
    _admin: None = Depends(require_lore_key_admin),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> dict[str, object]:
    if not ledger.delete_policy(policy_id):
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return {"policy_id": policy_id, "enabled": False}
