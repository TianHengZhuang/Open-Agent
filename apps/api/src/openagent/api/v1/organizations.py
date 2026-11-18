"""Organization endpoints used by the web app.

The dashboard needs the caller's organizations and permissions before it can
scope any request (workflows, agents, runs). Both are derived from active
memberships — never from a client-supplied id.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.api.dependencies import get_auth_context
from openagent.db.models import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationStatus,
)
from openagent.db.session import get_db
from openagent.services.authorization import AuthorizationService

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    status: str
    role: Optional[str] = None
    member_count: Optional[int] = None


class OrganizationListResponse(BaseModel):
    organizations: List[OrganizationResponse]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class PermissionsResponse(BaseModel):
    permissions: List[str]


def _slugify(value: str, fallback: str = "workspace") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48]
    return slug or fallback


@router.get("", response_model=OrganizationListResponse)
async def list_organizations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrganizationListResponse:
    """Organizations the current user is an active member of."""
    user, _, _ = await get_auth_context(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Authentication required", "code": "UNAUTHORIZED"},
        )

    rows = await db.execute(
        select(Organization, Membership)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(
            Membership.user_id == user.id,
            Membership.status == MembershipStatus.ACTIVE,
            Organization.deleted_at.is_(None),
        )
        .order_by(Organization.created_at)
    )
    organizations: List[OrganizationResponse] = []
    for org, membership in rows.all():
        organizations.append(
            OrganizationResponse(
                id=str(org.id),
                name=org.name,
                slug=org.slug,
                description=org.description,
                avatar_url=org.avatar_url,
                status=org.status.value if hasattr(org.status, "value") else str(org.status),
                role=(
                    membership.legacy_role.value
                    if hasattr(membership.legacy_role, "value")
                    else membership.legacy_role
                ),
            )
        )
    return OrganizationListResponse(organizations=organizations)


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    request: Request,
    data: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
) -> OrganizationResponse:
    """Create an organization with the caller as OWNER."""
    user, _, _ = await get_auth_context(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Authentication required", "code": "UNAUTHORIZED"},
        )

    slug = _slugify(data.name)
    taken = await db.execute(select(Organization.id).where(Organization.slug == slug))
    if taken.first():
        suffix = datetime.now(timezone.utc).strftime("%y%m%d%H%M")
        slug = f"{slug}-{suffix}"

    org = Organization(
        name=data.name.strip(),
        slug=slug,
        description=data.description,
        status=OrganizationStatus.ACTIVE,
        settings={},
    )
    db.add(org)
    await db.flush()
    db.add(
        Membership(
            user_id=user.id,
            organization_id=org.id,
            legacy_role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    await db.commit()

    return OrganizationResponse(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        description=org.description,
        avatar_url=org.avatar_url,
        status="active",
        role="OWNER",
    )


@router.get("/{organization_id}/my-permissions", response_model=PermissionsResponse)
async def my_permissions(
    request: Request,
    organization_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PermissionsResponse:
    """Permissions the caller holds in this organization."""
    user, _, _ = await get_auth_context(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Authentication required", "code": "UNAUTHORIZED"},
        )
    try:
        context = await AuthorizationService(db).get_user_context(user.id, organization_id)
    except Exception:
        context = None
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Not a member of this organization", "code": "FORBIDDEN"},
        )
    return PermissionsResponse(permissions=sorted(context.permissions))
