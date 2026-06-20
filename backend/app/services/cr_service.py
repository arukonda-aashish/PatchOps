"""CR lifecycle service — handles webhook processing and approval logic"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.change_request import ChangeRequest, CRStatus, CRPriority
from app.services.gemini_service import classify_cr

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "1": CRPriority.critical,
    "2": CRPriority.high,
    "3": CRPriority.medium,
    "4": CRPriority.low,
    "critical": CRPriority.critical,
    "high": CRPriority.high,
    "medium": CRPriority.medium,
    "low": CRPriority.low,
}


async def process_new_cr(payload) -> None:
    """
    Called from webhook background task.
    1. Upsert CR in DB
    2. Classify with Gemini
    3. If patching → set awaiting_approval; else → ignored
    """
    async with AsyncSessionLocal() as db:
        try:
            # Check if CR already exists
            result = await db.execute(
                select(ChangeRequest).where(ChangeRequest.cr_number == payload.cr_number)
            )
            cr = result.scalar_one_or_none()

            # Parse change window
            cw_start = None
            cw_end = None
            if payload.change_window_start:
                try:
                    cw_start = datetime.fromisoformat(
                        payload.change_window_start.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            if payload.change_window_end:
                try:
                    cw_end = datetime.fromisoformat(
                        payload.change_window_end.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            if not cr:
                sn_sys_id = payload.sys_id
                if not sn_sys_id:
                    sn_sys_id = await _fetch_cr_sys_id(payload.cr_number)
                cr = ChangeRequest(
                    cr_number=payload.cr_number,
                    title=payload.title or f"Change Request {payload.cr_number}",
                    description=payload.description,
                    sn_sys_id=sn_sys_id,
                    priority=PRIORITY_MAP.get(
                        (payload.priority or "medium").lower(), CRPriority.medium
                    ),
                    requested_by=payload.requested_by,
                    approver_name=payload.approver_name,
                    approver_email=payload.approver_email,
                    change_window_start=cw_start,
                    change_window_end=cw_end,
                    change_window_timezone=payload.change_window_timezone or "UTC",
                    status=CRStatus.queued,
                )
                db.add(cr)
                await db.flush()
                logger.info(f"Created new CR: {cr.cr_number}")
            else:
                # Update existing
                cr.title = payload.title or cr.title
                cr.description = payload.description or cr.description
                if cw_start:
                    cr.change_window_start = cw_start
                if cw_end:
                    cr.change_window_end = cw_end
                logger.info(f"Updated existing CR: {cr.cr_number}")

            # AI Classification
            logger.info(f"Classifying CR {cr.cr_number}...")
            classification = await classify_cr(cr.title, cr.description or "")
            cr.is_patching = classification.get("is_patching", False)
            cr.classification_confidence = classification.get("confidence", 0.0)
            cr.classification_reasoning = classification.get("reasoning", "")

            if cr.is_patching:
                cr.status = CRStatus.awaiting_approval
                logger.info(
                    f"CR {cr.cr_number} classified as PATCHING "
                    f"(confidence={cr.classification_confidence:.2f}) → awaiting approval"
                )
            else:
                cr.status = CRStatus.ignored
                logger.info(
                    f"CR {cr.cr_number} classified as NON-PATCHING → ignored"
                )

            await db.commit()

        except Exception as e:
            await db.rollback()
            logger.error(f"Error processing CR {payload.cr_number}: {e}", exc_info=True)

async def _fetch_cr_sys_id(cr_number: str) -> str | None:
    """Look up the ServiceNow sys_id for a CR by number — needed when webhook doesn't include it"""
    try:
        from app.services.servicenow_service import sn_client
        from app.core.config import settings
        if not settings.SERVICENOW_INSTANCE:
            return None
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.SERVICENOW_INSTANCE}/api/now/table/change_request",
                params={
                    "sysparm_query": f"number={cr_number}",
                    "sysparm_fields": "sys_id,number",
                    "sysparm_limit": "1",
                },
                auth=(settings.SERVICENOW_USER, settings.SERVICENOW_PASSWORD),
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            results = resp.json().get("result", [])
            if results:
                sys_id = results[0].get("sys_id")
                logger.info(f"Fetched sys_id for {cr_number}: {sys_id}")
                return sys_id
    except Exception as e:
        logger.warning(f"Could not fetch sys_id for {cr_number}: {e}")
    return None
async def process_cr_approval(payload) -> None:
    """
    Called when ServiceNow sends approval notification.
    Marks CR as approved and transitions to pending (waiting for change window).
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(ChangeRequest).where(ChangeRequest.cr_number == payload.cr_number)
            )
            cr = result.scalar_one_or_none()

            if not cr:
                logger.warning(f"Approval received for unknown CR: {payload.cr_number}")
                return

            if cr.status not in (CRStatus.awaiting_approval, CRStatus.queued):
                logger.warning(
                    f"CR {payload.cr_number} received approval but status is {cr.status}"
                )
                return

            # Record approval
            approved_at = None
            if payload.approved_at:
                try:
                    approved_at = datetime.fromisoformat(
                        payload.approved_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            approved_at = approved_at or datetime.now(timezone.utc)

            cr.approved_by = payload.approved_by or payload.approver_name or "ServiceNow"
            cr.approved_at = approved_at
            cr.status = CRStatus.pending

            logger.info(f"CR {payload.cr_number} approved by {cr.approved_by} → pending change window")
            await db.commit()

            # Queue the change window monitor
            from app.worker.celery_app import celery_app
            celery_app.send_task("monitor_change_window", args=[cr.id])

        except Exception as e:
            await db.rollback()
            logger.error(f"Error processing approval for {payload.cr_number}: {e}", exc_info=True)
