"""Integration: Financial anomaly detection (§11.8).

Tests the anomaly detection rules against known transaction patterns.
Idempotency: same transaction never produces duplicate alerts.
"""
import uuid
import pytest

pytestmark = pytest.mark.integration


def make_account(household_id: uuid.UUID, session) -> object:
    from app.modules.financial.models import FinancialAccount
    acct = FinancialAccount(
        household_id=household_id,
        household_connector_instance_id=uuid.uuid4(),
        external_account_ref=f"item_{uuid.uuid4().hex[:8]}",
        institution_name="Test Bank",
        status="active",
    )
    session.add(acct)
    return acct


@pytest.mark.asyncio
async def test_scam_detected_gift_card(db_session):
    """Gift card purchase triggers possible_scam alert."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    detector = AnomalyDetector(db_session)
    alerts = await detector.evaluate(account, {
        "transaction_id": f"txn_{uuid.uuid4().hex}",
        "merchant_name": "Google Play Gift Card",
        "amount": 100.00,
        "date": "2026-08-25",
    })

    assert len(alerts) >= 1
    assert any(a.alert_type == "possible_scam" for a in alerts)


@pytest.mark.asyncio
async def test_scam_detected_western_union(db_session):
    """Wire transfer to Western Union triggers possible_scam."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    detector = AnomalyDetector(db_session)
    alerts = await detector.evaluate(account, {
        "transaction_id": f"txn_{uuid.uuid4().hex}",
        "merchant_name": "Western Union Transfer",
        "amount": 500.00,
        "date": "2026-08-25",
    })
    assert any(a.alert_type == "possible_scam" for a in alerts)


@pytest.mark.asyncio
async def test_large_transaction_flagged(db_session):
    """Transaction > $500 always flagged as large_unusual_txn."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    detector = AnomalyDetector(db_session)
    alerts = await detector.evaluate(account, {
        "transaction_id": f"txn_{uuid.uuid4().hex}",
        "merchant_name": "Home Depot",
        "amount": 750.00,  # > $500 threshold
        "date": "2026-08-25",
    })
    assert any(a.alert_type == "large_unusual_txn" for a in alerts)


@pytest.mark.asyncio
async def test_normal_transaction_no_alert(db_session):
    """Ordinary grocery purchase produces no alert."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    detector = AnomalyDetector(db_session)
    alerts = await detector.evaluate(account, {
        "transaction_id": f"txn_{uuid.uuid4().hex}",
        "merchant_name": "Whole Foods Market",
        "amount": 87.43,
        "date": "2026-08-25",
    })
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_idempotency_no_duplicate_alerts(db_session):
    """Redelivered webhook for same transaction never creates duplicate alert (§11.8)."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    txn_id = f"txn_{uuid.uuid4().hex}"
    txn = {
        "transaction_id": txn_id,
        "merchant_name": "Bitcoin Exchange",
        "amount": 200.00,
        "date": "2026-08-25",
    }

    detector = AnomalyDetector(db_session)
    alerts1 = await detector.evaluate(account, txn)
    await db_session.flush()

    # Same transaction delivered again (Plaid webhook redelivery)
    alerts2 = await detector.evaluate(account, txn)

    assert len(alerts1) >= 1
    assert len(alerts2) == 0  # Idempotency — no duplicates


@pytest.mark.asyncio
async def test_credit_transactions_ignored(db_session):
    """Credits (refunds, deposits) never trigger alerts."""
    from app.modules.financial.anomaly import AnomalyDetector
    hh_id = uuid.uuid4()
    account = make_account(hh_id, db_session)
    await db_session.flush()

    detector = AnomalyDetector(db_session)
    # Negative amount = credit in Plaid
    alerts = await detector.evaluate(account, {
        "transaction_id": f"txn_{uuid.uuid4().hex}",
        "merchant_name": "Amazon Refund",
        "amount": -50.00,
        "date": "2026-08-25",
    })
    assert len(alerts) == 0
