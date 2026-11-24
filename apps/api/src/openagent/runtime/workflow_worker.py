"""Queue-mode worker entry point.

    WORKFLOW_QUEUE_MODE=true python -m openagent.runtime.workflow_worker

Claims queued workflow executions from the database and runs them until the
process is stopped. The API process only enqueues in this mode (see
``openagent.runtime.queue_mode``).
"""

from __future__ import annotations

import asyncio

from openagent.core.config import get_settings
from openagent.core.logging import configure_logging, get_logger
from openagent.db.session import ensure_schema, init_db, is_sqlite_url
from openagent.runtime.queue_mode import run_worker_loop, worker_id

logger = get_logger("openagent.workflows.worker")


async def main() -> None:
    configure_logging()
    settings = get_settings()
    init_db()
    if is_sqlite_url(settings.DATABASE_URL):
        await ensure_schema()
    from openagent.db import session as db_session

    maker = db_session.async_session_maker
    if maker is None:
        raise RuntimeError("database engine not initialized")
    logger.info("Workflow worker started", worker=worker_id())
    try:
        await run_worker_loop(maker)
    finally:
        await db_session.close_db()


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
