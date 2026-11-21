"""Legacy owner role permissions come from the system catalog.

Owners are the only organization-level role that is implicitly trusted with
every organization capability, so their permission set must track
``SYSTEM_PERMISSIONS`` instead of a hand-maintained list. Platform-only
permissions must never leak into an organization role.
"""

from openagent.db.models import MembershipRole
from openagent.db.models.permission import SYSTEM_PERMISSIONS
from openagent.services.authorization import AuthorizationService


def _catalog_permissions(resource: str | None = None) -> set[str]:
    return {
        f"{resource_name}:{action}"
        for resource_name, action, _description, _danger in SYSTEM_PERMISSIONS
        if resource is None or resource_name == resource
    }


def _owner_permissions() -> set[str]:
    # _get_legacy_permissions is pure: it never touches the database.
    return AuthorizationService(db=None)._get_legacy_permissions(MembershipRole.OWNER)


def test_owner_gets_every_organization_permission():
    expected = _catalog_permissions() - _catalog_permissions("platform")

    assert _owner_permissions() == expected


def test_owner_gets_no_platform_permissions():
    owner_permissions = _owner_permissions()
    platform_permissions = _catalog_permissions("platform")

    assert platform_permissions, "catalog must define platform permissions"
    assert owner_permissions.isdisjoint(platform_permissions)
    assert not {p for p in owner_permissions if p.startswith("platform:")}


def test_owner_covers_domains_added_after_the_legacy_map():
    # These domains were missing from the old hard-coded owner list, which
    # made pages such as Marketplace and Skills return 403 for owners.
    owner_permissions = _owner_permissions()

    assert {
        "marketplace:read",
        "package:read",
        "skill:read",
        "preset:read",
        "publisher:read",
        "review:read",
        "models:read",
        "providers:read",
    } <= owner_permissions
