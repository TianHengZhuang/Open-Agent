"""Workspace provisioning.

Every account needs an organization: the dashboard, workflows and runs are all
org-scoped. Registration and local mode both go through this module so the
rules stay in one place.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    User,
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)


def _slugify(value: str, fallback: str = "workspace") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48]
    return slug or fallback


async def ensure_personal_workspace(
    db: AsyncSession,
    user: User,
    *,
    seed_starter: bool = False,
) -> Optional[Organization]:
    """Give the user an OWNER workspace if they have none.

    Returns the created organization, or None when the user already belongs to
    one. The caller commits.
    """
    existing = await db.execute(
        select(Membership.organization_id).where(Membership.user_id == user.id).limit(1)
    )
    if existing.first() is not None:
        return None

    base = (user.display_name or user.email.split("@")[0] or "workspace").strip()
    slug = _slugify(base)
    conflict = await db.execute(select(Organization.id).where(Organization.slug == slug).limit(1))
    if conflict.first():
        slug = f"{slug}-{str(user.id)[:8]}"

    organization = Organization(
        name=f"{base[:60]} Workspace",
        slug=slug,
        status=OrganizationStatus.ACTIVE,
        settings={},
    )
    db.add(organization)
    await db.flush()
    db.add(
        Membership(
            user_id=user.id,
            organization_id=organization.id,
            legacy_role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    await db.flush()

    if seed_starter:
        await ensure_starter_workflow(db, organization)
    return organization


def starter_definition() -> dict:
    """A small n8n-style graph: trigger → set → if → two branches."""
    return {
        "schema_version": "1.0",
        "triggers": [
            {
                "id": "trigger",
                "type": "manual",
                "name": "Start",
                "config": {},
                "position": {"x": 80, "y": 180},
            }
        ],
        "nodes": [
            {
                "id": "set-fields",
                "type": "set",
                "name": "Set message",
                "config": {"values": {"message": "{{trigger.message}}"}},
                "position": {"x": 320, "y": 180},
            },
            {
                "id": "has-message",
                "type": "condition",
                "name": "Has message?",
                "config": {"expression": "{{nodes.set-fields.message}} != ''"},
                "position": {"x": 560, "y": 180},
            },
            {
                "id": "reply",
                "type": "transform",
                "name": "Build reply",
                "config": {"mapping": {"reply": "Hello {{nodes.set-fields.message}}"}},
                "position": {"x": 800, "y": 100},
            },
            {
                "id": "fallback",
                "type": "set",
                "name": "Default reply",
                "config": {"values": {"reply": "Hello there"}},
                "position": {"x": 800, "y": 260},
            },
        ],
        "edges": [
            {"id": "e1", "from": "trigger", "to": "set-fields"},
            {"id": "e2", "from": "set-fields", "to": "has-message"},
            {"id": "e3", "from": "has-message", "to": "reply", "label": "true"},
            {"id": "e4", "from": "has-message", "to": "fallback", "label": "false"},
        ],
        "variables": [],
        "settings": {},
    }


async def ensure_starter_workflow(db: AsyncSession, organization: Organization) -> Optional[Workflow]:
    """Create "My First Workflow" when the workspace has no workflows yet."""
    existing = await db.execute(
        select(Workflow.id).where(
            Workflow.organization_id == organization.id,
            Workflow.deleted_at.is_(None),
        ).limit(1)
    )
    if existing.first() is not None:
        return None

    workflow = Workflow(
        organization_id=organization.id,
        name="My First Workflow",
        slug="my-first-workflow",
        description="Manual trigger → set → if → two branches. Open it and press Test workflow.",
        status=WorkflowStatus.DRAFT,
        metadata={"tags": ["starter"]},
    )
    db.add(workflow)
    await db.flush()
    db.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version="v1",
            definition=starter_definition(),
            status="draft",
        )
    )
    await db.flush()
    return workflow
