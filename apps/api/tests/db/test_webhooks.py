"""Webhook model and repository behaviour."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.db.models import (
    Organization,
    OrganizationStatus,
    Webhook,
    WebhookStatus,
)
from openagent.db.repositories import WebhookRepository


@pytest_asyncio.fixture
async def org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="Test Org Webhooks",
        slug="test-org-webhooks",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


class TestWebhookModel:
    async def test_create_webhook(self, db_session: AsyncSession, org: Organization):
        webhook = Webhook(
            organization_id=org.id,
            name="Test Webhook",
            url="https://example.com/webhook",
            event_types=["user.created", "user.updated"],
            secret_hash="hashed-secret",
            status=WebhookStatus.ACTIVE,
        )
        db_session.add(webhook)
        await db_session.commit()
        await db_session.refresh(webhook)

        assert webhook.id is not None
        assert webhook.organization_id == org.id
        assert webhook.name == "Test Webhook"
        assert webhook.url == "https://example.com/webhook"
        assert webhook.event_types == ["user.created", "user.updated"]
        assert webhook.status == WebhookStatus.ACTIVE
        assert webhook.secret_hash == "hashed-secret"
        assert webhook.deleted_at is None

    async def test_soft_delete_webhook(self, db_session: AsyncSession, org: Organization):
        repo = WebhookRepository(db_session)
        webhook = await repo.create(
            organization_id=org.id,
            name="Disposable",
            url="https://example.com/hook",
            event_types=["user.created"],
            secret_hash="hash",
            status=WebhookStatus.INACTIVE,
        )

        deleted = await repo.soft_delete(webhook.id)

        assert deleted is not None
        assert deleted.deleted_at is not None


class TestWebhookRepository:
    async def test_list_active_filters_status(
        self, db_session: AsyncSession, org: Organization
    ):
        repo = WebhookRepository(db_session)
        await repo.create(
            organization_id=org.id,
            name="Active",
            url="https://example.com/active",
            event_types=["user.created"],
            secret_hash="hash",
            status=WebhookStatus.ACTIVE,
        )
        await repo.create(
            organization_id=org.id,
            name="Inactive",
            url="https://example.com/inactive",
            event_types=["user.created"],
            secret_hash="hash",
            status=WebhookStatus.INACTIVE,
        )

        active = await repo.list_active(org.id)

        assert [webhook.name for webhook in active] == ["Active"]

    async def test_tenant_scoped_list(
        self, db_session: AsyncSession, org: Organization
    ):
        other_org = Organization(
            name="Other Org",
            slug="other-org-webhooks",
            status=OrganizationStatus.ACTIVE,
        )
        db_session.add(other_org)
        await db_session.flush()

        repo = WebhookRepository(db_session)
        await repo.create(
            organization_id=org.id,
            name="Mine",
            url="https://example.com/mine",
            event_types=["user.created"],
            secret_hash="hash",
            status=WebhookStatus.ACTIVE,
        )
        await repo.create(
            organization_id=other_org.id,
            name="Theirs",
            url="https://example.com/theirs",
            event_types=["user.created"],
            secret_hash="hash",
            status=WebhookStatus.ACTIVE,
        )

        mine = await repo.list(organization_id=org.id)

        assert [webhook.name for webhook in mine] == ["Mine"]
