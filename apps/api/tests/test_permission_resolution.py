from app.core.database import Base  # noqa: F401
from app.models.identity import Permission, Role, User, UserPermissionOverride
from app.services.permissions import effective_permissions, has_permission


def permission(resource: str, action: str) -> Permission:
    return Permission(resource=resource, action=action)


def test_role_grant_and_user_allow_are_resolved():
    user = User(
        is_superuser=False,
        roles=[Role(name="Reader", permissions=[permission("customers", "read")])],
        permission_overrides=[UserPermissionOverride(permission=permission("orders", "read"), effect="allow")],
    )
    assert effective_permissions(user) == {"customers:read", "orders:read"}
    assert has_permission(user, "customers:read")
    assert has_permission(user, "orders:read")


def test_explicit_deny_removes_role_grant_and_overrides_win():
    user = User(
        is_superuser=False,
        roles=[Role(name="Reader", permissions=[permission("customers", "read")])],
        permission_overrides=[UserPermissionOverride(permission=permission("customers", "read"), effect="deny")],
    )
    assert "customers:read" not in effective_permissions(user)
    assert not has_permission(user, "customers:read")


def test_deny_wins_even_if_duplicate_override_rows_conflict():
    user = User(
        is_superuser=False,
        permission_overrides=[
            UserPermissionOverride(permission=permission("orders", "read"), effect="allow"),
            UserPermissionOverride(permission=permission("orders", "read"), effect="deny"),
        ],
    )
    assert "orders:read" not in effective_permissions(user)


def test_superuser_bypasses_permission_overrides():
    user = User(
        is_superuser=True,
        permission_overrides=[UserPermissionOverride(permission=permission("users", "read"), effect="deny")],
    )
    assert effective_permissions(user) == {"*"}
    assert has_permission(user, "users:read")
