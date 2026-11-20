from app.models.identity import User


def effective_permissions(user: User) -> set[str]:
    """Resolve role grants and user overrides consistently for every caller."""
    if user.is_superuser:
        return {"*"}

    grants = {
        f"{permission.resource}:{permission.action}"
        for role in user.roles
        for permission in role.permissions
    }
    allowed = set()
    denied = set()
    for override in user.permission_overrides:
        name = f"{override.permission.resource}:{override.permission.action}"
        (allowed if override.effect == "allow" else denied).add(name)
    return (grants | allowed) - denied


def has_permission(user: User, permission: str) -> bool:
    resolved = effective_permissions(user)
    return "*" in resolved or permission in resolved
