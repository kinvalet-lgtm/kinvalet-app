"""Operations module FastAPI routes — item lifecycle, approvals, delegation."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import get_db_session
from app.platform.context import set_request_context
from app.modules.identity.router import get_current_member
from app.modules.operations.models import (
    ApprovalRequest,
    ItemCorrection,
    OperationalItem,
    TaskDelegation,
)
from app.platform.observability import get_logger

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])
logger = get_logger(__name__)


class ItemResponse(BaseModel):
    id: uuid.UUID
    household_id: uuid.UUID
    category: str
    title: str
    status: str
    priority: str
    assigned_to_member_id: Optional[uuid.UUID] = None
    start_at: Optional[datetime] = None
    location: Optional[str] = None
    cost_cents: Optional[int] = None
    requires_approval: bool
    calendar_sync_status: str
    is_archived: bool


class CreateItemRequest(BaseModel):
    category: str
    title: str
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    cost_cents: Optional[int] = None
    assigned_to_member_id: Optional[uuid.UUID] = None
    about_member_id: Optional[uuid.UUID] = None
    priority: str = "normal"


class ConfirmItemRequest(BaseModel):
    confirmed: bool
    channel: str = "dashboard"  # dashboard | whatsapp_button


class DelegateItemRequest(BaseModel):
    delegated_to_member_id: uuid.UUID


class ApproveItemRequest(BaseModel):
    approved: bool
    channel: str = "dashboard"


@router.get("/items", response_model=list[ItemResponse])
async def list_items(
    archived: bool = False,
    category: Optional[str] = None,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """List household operational items."""
    set_request_context(
        household_id=str(current_member.household_id),
        member_id=str(current_member.id),
        actor_type="household_member",
    )

    stmt = (
        select(OperationalItem)
        .where(OperationalItem.household_id == current_member.household_id)
        .where(OperationalItem.is_archived == archived)
    )
    if category:
        stmt = stmt.where(OperationalItem.category == category)

    result = await session.execute(stmt.order_by(OperationalItem.created_at.desc()))
    items = result.scalars().all()
    return [_to_response(i) for i in items]


@router.post("/items", response_model=ItemResponse, status_code=201)
async def create_item(
    body: CreateItemRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Create an operational item manually (dashboard-created)."""
    if current_member.role == "secondary_readonly":
        raise HTTPException(status_code=403, detail="Read-only members cannot create items directly")

    requires_approval = (body.cost_cents is not None and body.cost_cents > 0)
    item = OperationalItem(
        household_id=current_member.household_id,
        category=body.category,
        title=body.title,
        start_at=body.start_at,
        end_at=body.end_at,
        location=body.location,
        cost_cents=body.cost_cents,
        requires_approval=requires_approval,
        assigned_to_member_id=body.assigned_to_member_id,
        about_member_id=body.about_member_id,
        priority=body.priority,
        status="confirmed",  # Dashboard-created items start confirmed
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _to_response(item)


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == item_id)
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return _to_response(item)


@router.post("/items/{item_id}/confirm")
async def confirm_item(
    item_id: uuid.UUID,
    body: ConfirmItemRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Confirm or decline a pending item (§11.4)."""
    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == item_id)
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.status not in ("pending_confirmation", "pending_approval"):
        raise HTTPException(
            status_code=409,
            detail=f"Item is in state '{item.status}' and cannot be confirmed/declined now"
        )

    if not body.confirmed:
        item.status = "declined"
    else:
        item.status = "confirmed"

    await session.commit()
    return {"status": item.status}


@router.post("/items/{item_id}/delegate")
async def delegate_item(
    item_id: uuid.UUID,
    body: DelegateItemRequest,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Manually delegate an item to another member (§11.5)."""
    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == item_id)
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # Do NOT change assigned_to_member_id yet — stays with delegator until accepted
    delegation = TaskDelegation(
        operational_item_id=item.id,
        household_id=item.household_id,
        delegated_by_member_id=current_member.id,
        delegated_to_member_id=body.delegated_to_member_id,
        trigger_reason="manual",
        status="proposed",
    )
    session.add(delegation)

    # Publish event
    from app.contracts.events import DelegationProposed
    from app.platform.events.outbox import add_event_to_outbox
    import json
    await add_event_to_outbox(
        session,
        "DelegationProposed",
        {
            "delegation_id": str(delegation.id),
            "operational_item_id": str(item.id),
            "household_id": str(item.household_id),
            "delegated_by_member_id": str(current_member.id),
            "delegated_to_member_id": str(body.delegated_to_member_id),
            "item_title": item.title,
            "trigger_reason": "manual",
        }
    )

    await session.commit()
    return {"delegation_id": str(delegation.id)}


@router.post("/items/{item_id}/complete")
async def complete_item(
    item_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Mark an item as completed."""
    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == item_id)
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.status == "completed":
        return {"status": "completed"}

    item.status = "completed"
    await session.commit()
    return {"status": "completed"}


@router.post("/items/{item_id}/archive")
async def archive_item(
    item_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Archive an item (§11.12). Never deletes — only removes from default views."""
    result = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.id == item_id)
        .where(OperationalItem.household_id == current_member.household_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # Check for pending delegations (§11.12 edge case)
    pending_delegation = await session.execute(
        select(TaskDelegation)
        .where(TaskDelegation.operational_item_id == item_id)
        .where(TaskDelegation.status == "proposed")
    )
    if pending_delegation.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="This item has a pending delegation. Resolve it before archiving."
        )

    item.is_archived = True
    item.archived_at = datetime.utcnow()
    item.archived_by_member_id = current_member.id
    await session.commit()
    return {"archived": True}


@router.get("/items/my-tasks/{member_id}")
async def my_tasks(
    member_id: uuid.UUID,
    current_member=Depends(get_current_member),
    session: AsyncSession = Depends(get_db_session),
):
    """Return the current member's task buckets (WhatsApp parity, §11.12)."""
    if current_member.id != member_id and current_member.role != "primary_admin":
        raise HTTPException(status_code=403, detail="Access denied")

    # Pending delegations
    delegations = await session.execute(
        select(TaskDelegation)
        .where(TaskDelegation.delegated_to_member_id == member_id)
        .where(TaskDelegation.household_id == current_member.household_id)
        .where(TaskDelegation.status == "proposed")
    )

    # Assigned items
    assigned = await session.execute(
        select(OperationalItem)
        .where(OperationalItem.assigned_to_member_id == member_id)
        .where(OperationalItem.household_id == current_member.household_id)
        .where(OperationalItem.status.in_(["confirmed", "pending_confirmation", "pending_approval"]))
        .where(OperationalItem.is_archived == False)
    )

    return {
        "awaiting_response": [str(d.id) for d in delegations.scalars()],
        "assigned_to_you": [_to_response(i) for i in assigned.scalars()],
    }


def _to_response(item: OperationalItem) -> ItemResponse:
    return ItemResponse(
        id=item.id,
        household_id=item.household_id,
        category=item.category,
        title=item.title,
        status=item.status,
        priority=item.priority,
        assigned_to_member_id=item.assigned_to_member_id,
        start_at=item.start_at,
        location=item.location,
        cost_cents=item.cost_cents,
        requires_approval=item.requires_approval,
        calendar_sync_status=item.calendar_sync_status,
        is_archived=item.is_archived,
    )
