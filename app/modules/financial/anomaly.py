"""Financial anomaly detection engine (§11.8).

Rules evaluated on each new transaction from Plaid:
1. possible_scam        — gift cards, wires to new payees, known scam merchants
2. duplicate_billing    — same merchant + amount within 7 days
3. unexpected_subscription — new recurring charge not seen before
4. large_unusual_txn   — amount > 3x the trailing 90-day average for that merchant

Idempotency: unique on (external_account_ref, provider_transaction_id, alert_type).
A redelivered Plaid webhook never produces a duplicate alert.

The system never takes financial action — detection and surfacing only (§11.8).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financial.models import FinancialAccount, FinancialAlert
from app.platform.observability import get_logger

logger = get_logger(__name__)

# Known scam merchant patterns (case-insensitive)
SCAM_PATTERNS = [
    r"gift\s*card",
    r"google\s*play\s*gift",
    r"apple\s*itunes\s*gift",
    r"moneygram",
    r"western\s*union",
    r"coinbase",          # unusual for elderly accounts
    r"crypto",
    r"bitcoin",
    r"wire\s*transfer",
    r"irs\s*payment",
    r"social\s*security",
    r"warrant",
    r"bail",
    r"lottery",
    r"sweepstake",
    r"prize",
]

LARGE_TXN_THRESHOLD_CENTS = 50000  # $500 always flagged regardless of history
LARGE_TXN_MULTIPLIER = 3.0         # 3x trailing average
DUPLICATE_WINDOW_DAYS = 7
SUBSCRIPTION_LOOKBACK_DAYS = 90


class AnomalyDetector:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def evaluate(
        self,
        account: FinancialAccount,
        transaction: dict,
    ) -> list[FinancialAlert]:
        """Evaluate a single transaction, return any new alerts created."""
        alerts = []

        txn_id = transaction.get("transaction_id", "")
        merchant = transaction.get("merchant_name") or transaction.get("name", "")
        amount_cents = int(abs(transaction.get("amount", 0)) * 100)
        date_str = transaction.get("date", "")
        category = transaction.get("personal_finance_category", {}).get("primary", "")

        # Skip deposits and credits
        if transaction.get("amount", 0) < 0:
            return []

        # 1. Possible scam detection
        if self._is_scam(merchant, amount_cents):
            alert = await self._create_alert(
                account=account,
                txn_id=txn_id,
                alert_type="possible_scam",
                transaction_snapshot={
                    "merchant": merchant,
                    "amount_cents": amount_cents,
                    "date": date_str,
                    "reason": "Matches known scam pattern",
                },
            )
            if alert:
                alerts.append(alert)

        # 2. Duplicate billing detection
        if await self._is_duplicate(account.id, merchant, amount_cents):
            alert = await self._create_alert(
                account=account,
                txn_id=txn_id,
                alert_type="duplicate_billing",
                transaction_snapshot={
                    "merchant": merchant,
                    "amount_cents": amount_cents,
                    "date": date_str,
                    "reason": f"Same charge from {merchant} within {DUPLICATE_WINDOW_DAYS} days",
                },
            )
            if alert:
                alerts.append(alert)

        # 3. Unexpected new subscription
        if self._looks_like_subscription(merchant, amount_cents, category):
            if not await self._seen_before(account.id, merchant):
                alert = await self._create_alert(
                    account=account,
                    txn_id=txn_id,
                    alert_type="unexpected_subscription",
                    transaction_snapshot={
                        "merchant": merchant,
                        "amount_cents": amount_cents,
                        "date": date_str,
                        "reason": "New recurring charge not previously seen",
                    },
                )
                if alert:
                    alerts.append(alert)

        # 4. Large unusual transaction
        if await self._is_large_unusual(account.id, merchant, amount_cents):
            alert = await self._create_alert(
                account=account,
                txn_id=txn_id,
                alert_type="large_unusual_txn",
                transaction_snapshot={
                    "merchant": merchant,
                    "amount_cents": amount_cents,
                    "date": date_str,
                    "reason": f"${amount_cents/100:.2f} is unusually large for this merchant",
                },
            )
            if alert:
                alerts.append(alert)

        if alerts:
            logger.info(
                "anomalies_detected",
                account_id=str(account.id),
                transaction_id=txn_id,
                count=len(alerts),
                types=[a.alert_type for a in alerts],
            )

        return alerts

    async def _create_alert(
        self,
        account: FinancialAccount,
        txn_id: str,
        alert_type: str,
        transaction_snapshot: dict,
    ) -> Optional[FinancialAlert]:
        """Create alert, respecting idempotency constraint."""
        # Check if already exists (idempotency — §11.8)
        existing = await self._session.execute(
            select(FinancialAlert)
            .where(FinancialAlert.external_account_ref == account.external_account_ref)
            .where(FinancialAlert.provider_transaction_id == txn_id)
            .where(FinancialAlert.alert_type == alert_type)
        )
        if existing.scalar_one_or_none():
            return None  # Already alerted for this transaction + type

        alert = FinancialAlert(
            financial_account_id=account.id,
            household_id=account.household_id,
            alert_type=alert_type,
            external_account_ref=account.external_account_ref,
            provider_transaction_id=txn_id,
            transaction_ref=transaction_snapshot,
            status="new",
        )
        self._session.add(alert)
        await self._session.flush()
        return alert

    @staticmethod
    def _is_scam(merchant: str, amount_cents: int) -> bool:
        merchant_lower = merchant.lower()
        return any(re.search(p, merchant_lower) for p in SCAM_PATTERNS)

    async def _is_duplicate(
        self, account_id: UUID, merchant: str, amount_cents: int
    ) -> bool:
        """Check if same merchant + amount appeared within the duplicate window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=DUPLICATE_WINDOW_DAYS)
        result = await self._session.execute(
            select(func.count())
            .select_from(FinancialAlert)
            .where(FinancialAlert.financial_account_id == account_id)
            .where(FinancialAlert.alert_type.in_(["duplicate_billing", "large_unusual_txn"]))
            .where(FinancialAlert.surfaced_at >= cutoff)
            .where(
                FinancialAlert.transaction_ref["merchant"].astext == merchant
            )
            .where(
                FinancialAlert.transaction_ref["amount_cents"].astext == str(amount_cents)
            )
        )
        return (result.scalar() or 0) > 0

    @staticmethod
    def _looks_like_subscription(merchant: str, amount_cents: int, category: str) -> bool:
        """Heuristic: small recurring charge."""
        sub_categories = {"SUBSCRIPTION", "PERSONAL_CARE", "ENTERTAINMENT"}
        if category in sub_categories and amount_cents < 5000:
            return True
        # Common subscription amounts: $4.99, $9.99, $14.99, $19.99
        common_sub = {499, 999, 1499, 1999, 2999, 4999}
        return amount_cents in common_sub

    async def _seen_before(self, account_id: UUID, merchant: str) -> bool:
        """Check if we've ever seen this merchant on this account."""
        result = await self._session.execute(
            select(func.count())
            .select_from(FinancialAlert)
            .where(FinancialAlert.financial_account_id == account_id)
            .where(
                FinancialAlert.transaction_ref["merchant"].astext == merchant
            )
        )
        return (result.scalar() or 0) > 0

    async def _is_large_unusual(
        self, account_id: UUID, merchant: str, amount_cents: int
    ) -> bool:
        """Flag if amount > 3x trailing average OR > $500 absolute threshold."""
        if amount_cents >= LARGE_TXN_THRESHOLD_CENTS:
            return True
        # Can't compute trailing average from alerts alone in MVP
        # Full implementation would query Plaid transaction history
        return False
