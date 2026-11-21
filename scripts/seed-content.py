"""Seed starter catalog content into the local database.

Creates global Marketplace categories, a handful of official template
packages, and a few official skills so the Marketplace, Template Center and
Skills pages have content to show in a fresh local install.

The script is idempotent: rows are matched by slug and skipped when they
already exist. Run it from the repository root:

    apps/api/.venv/Scripts/python.exe scripts/seed-content.py     # Windows
    apps/api/.venv/bin/python scripts/seed-content.py             # macOS/Linux

It uses the same ``.env`` / DATABASE_URL as the API, so stop nothing — the
local SQLite database accepts the writes while the API is running.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Settings load `.env` from the working directory; pin it to the repo root.
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from sqlalchemy import select  # noqa: E402

from openagent.db.models import (  # noqa: E402
    MarketplaceCategory,
    PackageResource,
    PackageVersion,
    PackageVersionStatus,
    PackageVisibility,
    PackageTrust,
    ReusablePackage,
    ReusablePackageType,
    Skill,
    SkillVersion,
)
from openagent.db import session as db_session  # noqa: E402
from openagent.packages.types import PACKAGE_FORMAT, PACKAGE_FORMAT_VERSION  # noqa: E402

NOW = datetime.now(timezone.utc)
VERSION = "1.0.0"

CATEGORIES = [
    ("ai-agents", "AI & Agents", "Autonomous agents, copilots and workforces.", 10),
    ("automation", "Automation", "Event-driven workflows and scheduled jobs.", 20),
    ("customer-support", "Customer Support", "Triage, replies and helpdesk automations.", 30),
    ("data", "Data", "Collection, enrichment, reporting and analytics.", 40),
    ("marketing", "Marketing", "Campaigns, content and lead nurturing.", 50),
    ("developer-tools", "Developer Tools", "Engineering, code review and release helpers.", 60),
]

PACKAGES = [
    {
        "slug": "daily-inbox-triage",
        "name": "Daily Inbox Triage",
        "type": ReusablePackageType.WORKFLOW,
        "description": (
            "Sorts new mail into urgent, needs-reply and FYI buckets, drafts "
            "short replies for the routine ones and posts a digest to chat."
        ),
        "categories": ["Automation", "Productivity"],
        "tags": ["email", "triage", "digest"],
        "resources": [("WORKFLOW", "daily-inbox-triage", "Daily Inbox Triage")],
    },
    {
        "slug": "support-triage-agent",
        "name": "Support Triage Agent",
        "type": ReusablePackageType.AGENT,
        "description": (
            "Classifies incoming support tickets, assigns priority and team, "
            "and suggests the best matching help-center article."
        ),
        "categories": ["Customer Support", "AI & Agents"],
        "tags": ["support", "classification", "rag"],
        "resources": [("AGENT", "support-triage-agent", "Support Triage Agent")],
    },
    {
        "slug": "lead-intake-to-crm",
        "name": "Lead Intake to CRM",
        "type": ReusablePackageType.AUTOMATION_RECIPE,
        "description": (
            "Normalizes form submissions, enriches the company, scores fit and "
            "creates or updates the CRM record with an owner assignment."
        ),
        "categories": ["Sales", "Automation"],
        "tags": ["crm", "leads", "enrichment"],
        "resources": [("WORKFLOW", "lead-intake-to-crm", "Lead Intake to CRM")],
    },
    {
        "slug": "weekly-ops-report",
        "name": "Weekly Ops Report",
        "type": ReusablePackageType.WORKFLOW,
        "description": (
            "Pulls execution metrics from OpenAgent, highlights failures and "
            "slow nodes, and delivers a one-page summary every Monday."
        ),
        "categories": ["Data", "Productivity"],
        "tags": ["reporting", "metrics", "schedule"],
        "resources": [("WORKFLOW", "weekly-ops-report", "Weekly Ops Report")],
    },
]

SKILLS = [
    {
        "slug": "web-research-brief",
        "name": "Web Research Brief",
        "description": "Gathers sources on a topic and returns a cited two-page brief.",
        "categories": ["Research", "AI & Agents"],
        "tags": ["research", "summarization", "citations"],
        "instructions": (
            "Search at least three independent sources, prefer primary material, "
            "record URLs for every claim, and finish with a short 'What we still "
            "don't know' section."
        ),
    },
    {
        "slug": "email-drafting",
        "name": "Email Drafting",
        "description": "Writes concise, on-tone replies with a clear next step.",
        "categories": ["Productivity"],
        "tags": ["email", "writing"],
        "instructions": (
            "Match the thread's tone, keep the reply under 150 words, lead with "
            "the answer, and close with exactly one call to action."
        ),
    },
    {
        "slug": "meeting-notes-to-actions",
        "name": "Meeting Notes to Actions",
        "description": "Turns raw meeting notes into decisions, owners and due dates.",
        "categories": ["Productivity", "Business"],
        "tags": ["meetings", "actions", "summarization"],
        "instructions": (
            "Extract decisions, action items with an owner and due date, and open "
            "questions. Omit small talk and never invent an owner."
        ),
    },
    {
        "slug": "data-summarization",
        "name": "Data Summarization",
        "description": "Summarizes tables and metrics into trends, outliers and caveats.",
        "categories": ["Data"],
        "tags": ["analytics", "reporting"],
        "instructions": (
            "Describe the shape of the data first, then the top three trends, any "
            "outliers, and the confidence limits of the summary."
        ),
    },
]


def _manifest(slug: str, name: str, package_type: ReusablePackageType,
              description: str, categories: list[str], tags: list[str],
              resources: list[tuple[str, str, str]]) -> dict:
    return {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "package": {
            "id": slug,
            "name": name,
            "version": VERSION,
            "type": package_type.value,
        },
        "author": {"name": "OpenAgent"},
        "license": "Apache-2.0",
        "visibility": PackageVisibility.PUBLIC.value,
        "trust": PackageTrust.CORE.value,
        "description": description,
        "categories": categories,
        "tags": tags,
        "resources": [
            {"kind": kind, "slug": resource_slug, "name": resource_name, "payload": {}}
            for kind, resource_slug, resource_name in resources
        ],
        "changelog": "Initial starter release.",
    }


async def seed_categories(session) -> int:
    created = 0
    for slug, name, description, position in CATEGORIES:
        exists = (await session.execute(select(MarketplaceCategory).where(
            MarketplaceCategory.marketplace_id.is_(None),
            MarketplaceCategory.slug == slug,
        ))).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(MarketplaceCategory(
            marketplace_id=None, slug=slug, name=name, description=description,
            position=position, official=True,
        ))
        created += 1
    return created


async def seed_packages(session) -> int:
    created = 0
    for spec in PACKAGES:
        exists = (await session.execute(select(ReusablePackage).where(
            ReusablePackage.organization_id.is_(None),
            ReusablePackage.slug == spec["slug"],
        ))).scalar_one_or_none()
        if exists is not None:
            continue
        package = ReusablePackage(
            organization_id=None,
            slug=spec["slug"],
            name=spec["name"],
            description=spec["description"],
            package_type=spec["type"],
            visibility=PackageVisibility.PUBLIC,
            trust=PackageTrust.CORE,
            official=True,
            license="Apache-2.0",
            author_name="OpenAgent",
            publisher="OpenAgent",
            categories=list(spec["categories"]),
            tags=list(spec["tags"]),
            latest_version=VERSION,
        )
        version = PackageVersion(
            version=VERSION,
            status=PackageVersionStatus.PUBLISHED,
            manifest=_manifest(
                spec["slug"], spec["name"], spec["type"], spec["description"],
                list(spec["categories"]), list(spec["tags"]), spec["resources"],
            ),
            risk="LOW",
            changelog="Initial starter release.",
            published_at=NOW,
        )
        for kind, resource_slug, resource_name in spec["resources"]:
            version.resources.append(PackageResource(
                kind=kind, slug=resource_slug, name=resource_name, payload={},
            ))
        package.versions.append(version)
        session.add(package)
        created += 1
    return created


async def seed_skills(session) -> int:
    created = 0
    for spec in SKILLS:
        exists = (await session.execute(select(Skill).where(
            Skill.organization_id.is_(None),
            Skill.slug == spec["slug"],
        ))).scalar_one_or_none()
        if exists is not None:
            continue
        skill = Skill(
            organization_id=None,
            slug=spec["slug"],
            name=spec["name"],
            description=spec["description"],
            status=PackageVersionStatus.PUBLISHED,
            trust=PackageTrust.CORE,
            visibility=PackageVisibility.PUBLIC,
            official=True,
            categories=list(spec["categories"]),
            tags=list(spec["tags"]),
            latest_version=VERSION,
        )
        skill.versions.append(SkillVersion(
            version=VERSION,
            status=PackageVersionStatus.PUBLISHED,
            instructions=spec["instructions"],
            published_at=NOW,
        ))
        session.add(skill)
        created += 1
    return created


async def main() -> None:
    db_session.init_db()
    await db_session.ensure_schema()
    async with db_session.async_session_maker() as session:
        categories = await seed_categories(session)
        packages = await seed_packages(session)
        skills = await seed_skills(session)
        await session.commit()

    total = categories + packages + skills
    if total == 0:
        print("Starter content already seeded; nothing to do.")
        return
    print(
        f"Seeded {categories} marketplace categories, "
        f"{packages} template packages and {skills} skills."
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ModuleNotFoundError as exc:  # pragma: no cover - convenience
        raise SystemExit(
            f"Missing dependency ({exc}). Run this with the API virtualenv:\n"
            "  apps/api/.venv/Scripts/python.exe scripts/seed-content.py"
        ) from exc
