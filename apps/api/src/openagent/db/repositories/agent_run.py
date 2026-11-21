"""Agent Run repository."""

from __future__ import annotations

from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models import AgentRun, AgentRunStatus
from openagent.db.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository[AgentRun]):
    def __init__(self, session: AsyncSession):
        super().__init__(AgentRun, session)

    async def create(
        self,
        agent_id: UUID,
        organization_id: Optional[UUID] = None,
        status: AgentRunStatus = AgentRunStatus.QUEUED,
        agent_version_id: Optional[UUID] = None,
        workflow_execution_id: Optional[UUID] = None,
        input_data: Optional[Dict] = None,
    ) -> AgentRun:
        if organization_id is None:
            from openagent.db.models import Agent

            result = await self.session.execute(
                select(Agent.organization_id).where(Agent.id == agent_id)
            )
            organization_id = result.scalar_one_or_none()
            if organization_id is None:
                raise ValueError(f"Agent {agent_id} not found for run creation")
        run = AgentRun(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            workflow_execution_id=workflow_execution_id,
            input=input_data or {},
            status=status,
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_by_id(self, run_id: UUID) -> Optional[AgentRun]:
        result = await self.session.execute(
            select(AgentRun).where(AgentRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_by_agent(
        self,
        agent_id: UUID,
        organization_id: UUID,
        status: Optional[AgentRunStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> List[AgentRun]:
        query = select(AgentRun).where(
            AgentRun.agent_id == agent_id,
            AgentRun.organization_id == organization_id,
        )
        if status:
            query = query.where(AgentRun.status == status)
        
        query = query.order_by(AgentRun.created_at.desc())
        query = query.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_by_agent(self, agent_id: UUID, organization_id: UUID, status: Optional[AgentRunStatus] = None) -> int:
        query = select(func.count(AgentRun.id)).where(
            AgentRun.agent_id == agent_id,
            AgentRun.organization_id == organization_id,
        )
        if status:
            query = query.where(AgentRun.status == status)
        result = await self.session.execute(query)
        return result.scalar_one()