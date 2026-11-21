from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models.execution_event import ExecutionEvent
from openagent.db.repositories.base import BaseRepository


class ExecutionEventRepository(BaseRepository[ExecutionEvent]):
    """Append-only execution events with per-run sequence numbering."""

    def __init__(self, session: AsyncSession):
        super().__init__(ExecutionEvent, session)

    async def create_event(
        self,
        organization_id: UUID,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        run_id: Optional[UUID] = None,
        workflow_execution_id: Optional[UUID] = None,
        timestamp: Optional[datetime] = None,
    ) -> ExecutionEvent:
        sequence_query = select(
            func.coalesce(func.max(ExecutionEvent.sequence), 0)
        ).where(ExecutionEvent.organization_id == organization_id)
        if run_id is not None:
            sequence_query = sequence_query.where(ExecutionEvent.run_id == run_id)
        elif workflow_execution_id is not None:
            sequence_query = sequence_query.where(
                ExecutionEvent.workflow_execution_id == workflow_execution_id
            )
        else:
            sequence_query = sequence_query.where(
                ExecutionEvent.run_id.is_(None),
                ExecutionEvent.workflow_execution_id.is_(None),
            )
        current = int((await self.session.execute(sequence_query)).scalar_one() or 0)

        event = ExecutionEvent(
            organization_id=organization_id,
            run_id=run_id,
            workflow_execution_id=workflow_execution_id,
            event_type=event_type,
            sequence=current + 1,
            timestamp=timestamp or datetime.now(timezone.utc),
            payload=payload or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event