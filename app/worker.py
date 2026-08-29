"""Worker entrypoint — procrastinate worker + outbox dispatcher + all scheduler loops.

Two containers, deliberately:
- API: handles HTTP, webhooks — must respond in <500ms
- Worker: LLM extraction (6s), sends notifications, fires skills, sends briefings,
          materializes recurrences, runs escalation ladder, auto-archives
"""
import asyncio
from datetime import datetime, timezone

from app.platform.observability import get_logger

logger = get_logger(__name__)


async def run_skills_loop() -> None:
    """Poll skill_executions every 30 seconds."""
    from app.modules.skills.scheduler import run_skills_scheduler
    logger.info("skills_scheduler_started")
    while True:
        try:
            await run_skills_scheduler()
        except Exception as e:
            logger.error("skills_scheduler_error", error=str(e))
        await asyncio.sleep(30)


async def run_briefing_loop() -> None:
    """Poll for households due for 8 AM briefing every 60 seconds."""
    from app.modules.briefing.scheduler import run_briefing_scheduler
    logger.info("briefing_scheduler_started")
    while True:
        try:
            await run_briefing_scheduler()
        except Exception as e:
            logger.error("briefing_scheduler_error", error=str(e))
        await asyncio.sleep(60)


async def run_maintenance_loop() -> None:
    """Run maintenance tasks every hour: archive, recurrence, escalation."""
    from app.platform.db import AsyncSessionFactory
    logger.info("maintenance_scheduler_started")
    while True:
        try:
            await asyncio.sleep(3600)  # Run hourly
            async with AsyncSessionFactory() as session:
                from sqlalchemy import text

                # Fetch all active households
                result = await session.execute(
                    text("SELECT id FROM identity.household WHERE status = 'active'")
                )
                household_ids = [str(row[0]) for row in result.fetchall()]

            for hh_id in household_ids:
                import uuid
                hh_uuid = uuid.UUID(hh_id)

                # Auto-archive completed items >30 days
                async with AsyncSessionFactory() as session:
                    from app.modules.operations.services.escalation import run_archive_scheduler
                    from app.platform.context import set_request_context
                    set_request_context(household_id=hh_id, actor_type="system_agent")
                    count = await run_archive_scheduler(hh_uuid, session)
                    await session.commit()

                # Materialize recurrence occurrences
                async with AsyncSessionFactory() as session:
                    from app.modules.operations.services.recurrence import RecurrenceMaterializer
                    from app.platform.context import set_request_context
                    set_request_context(household_id=hh_id, actor_type="system_agent")
                    materializer = RecurrenceMaterializer(session)
                    await materializer.materialize_all_active(hh_uuid)
                    await session.commit()

                # Run escalation check for critical items
                async with AsyncSessionFactory() as session:
                    from app.modules.operations.services.escalation import EscalationService
                    from app.platform.context import set_request_context
                    set_request_context(household_id=hh_id, actor_type="system_agent")
                    escalation_svc = EscalationService(session)
                    await escalation_svc.run_escalation_check(hh_uuid)
                    await session.commit()

            # Renew Gmail watches expiring within 24 hours
            async with AsyncSessionFactory() as session:
                from app.modules.connectors.gmail_sync import GmailWatchManager
                manager = GmailWatchManager(session)
                renewed = await manager.renew_expiring_watches()
                await session.commit()
                if renewed:
                    logger.info("gmail_watches_renewed", count=renewed)

            # Sync calendar events → create operational_items (4th input channel)
            for hh_id in household_ids:
                async with AsyncSessionFactory() as session:
                    try:
                        from app.modules.connectors.calendar_ingest import sync_calendar_events
                        from app.platform.context import set_request_context
                        set_request_context(household_id=hh_id, actor_type="system_agent")
                        count = await sync_calendar_events(session, uuid.UUID(hh_id))
                        await session.commit()
                    except Exception as e:
                        logger.warning("calendar_sync_failed", household_id=hh_id, error=str(e))

        except Exception as e:
            logger.error("maintenance_loop_error", error=str(e))


async def run_media_purge_loop() -> None:
    """Purge raw_media_url references older than 72 hours (§18.2 / §2.3 PII minimization)."""
    from app.platform.db import AsyncSessionFactory
    from sqlalchemy import text, update
    from datetime import timedelta
    logger.info("media_purge_started")
    while True:
        try:
            await asyncio.sleep(3600)  # Check hourly
            cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
            async with AsyncSessionFactory() as session:
                await session.execute(
                    text("""
                        UPDATE inbound.inbound_message
                        SET raw_media_url = NULL
                        WHERE raw_media_url IS NOT NULL
                          AND received_at < :cutoff
                    """),
                    {"cutoff": cutoff},
                )
                await session.commit()
                logger.info("media_purge_complete", cutoff=cutoff.isoformat())
        except Exception as e:
            logger.error("media_purge_error", error=str(e))


async def run_worker() -> None:
    """Run all worker loops concurrently."""
    from app.platform.events.bus import run_dispatcher_loop

    logger.info("worker_starting")

    # Register all event handlers
    import app.modules.notification.handlers  # noqa: F401
    import app.modules.operations.handlers    # noqa: F401

    # Register all tasks so procrastinate knows about them
    import app.modules.extraction.tasks  # noqa: F401
    import app.modules.briefing.tasks    # noqa: F401

    # Open procrastinate queue
    from app.platform.queue import async_queue_app
    await async_queue_app.open_async()
    logger.info("queue_opened")

    await asyncio.gather(
        run_dispatcher_loop(),     # outbox → event handlers
        run_skills_loop(),         # skill_executions → reminders/ETA
        run_briefing_loop(),       # 8 AM briefings
        run_maintenance_loop(),    # archive + recurrence + escalation (hourly)
        run_media_purge_loop(),    # 72h raw media purge (hourly)
    )


def main() -> None:
    logger.info("worker_process_starting")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
