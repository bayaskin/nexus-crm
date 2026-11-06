import argparse
from getpass import getpass

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.identity import Permission, Role, User

DEFAULT_PERMISSIONS = [
    ("users", "read"), ("users", "create"), ("users", "update"),
    ("roles", "read"), ("roles", "assign"),
    ("customers", "read"), ("customers", "create"), ("customers", "update"),
    ("products", "read"), ("products", "create"), ("products", "update"),
    ("inventory", "read"), ("inventory", "adjust"),
    ("orders", "read"), ("orders", "create"), ("orders", "update"),
]

ROLE_GRANTS = {
    "Sales Manager": [
        "customers:read", "customers:create", "customers:update",
        "products:read", "inventory:read",
        "orders:read", "orders:create", "orders:update",
    ],
    "Sales Representative": [
        "customers:read", "customers:create", "products:read",
        "inventory:read", "orders:read", "orders:create",
    ],
    "Support": ["customers:read", "orders:read", "products:read"],
    "Read-only": ["customers:read", "orders:read", "products:read", "inventory:read"],
}



def main() -> None:
    parser = argparse.ArgumentParser(description="Create the initial Nexus administrator")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass("Administrator password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")

    db = SessionLocal()
    try:
        email = args.email.strip().lower()
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            raise SystemExit(f"User {email} already exists; no changes made")

        permissions = {}
        for resource, action in DEFAULT_PERMISSIONS:
            permission = db.scalar(
                select(Permission).where(
                    Permission.resource == resource,
                    Permission.action == action,
                )
            )
            if permission is None:
                permission = Permission(resource=resource, action=action)
                db.add(permission)
                db.flush()
            permissions[f"{resource}:{action}"] = permission

        all_role_names = ["Administrator", *ROLE_GRANTS.keys()]
        roles = {}
        for role_name in all_role_names:
            role = db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name, description=f"{role_name} access role")
                db.add(role)
                db.flush()
            roles[role_name] = role

        grants = {
            "Administrator": list(permissions),
            **ROLE_GRANTS,
        }
        for role_name, permission_names in grants.items():
            role = roles[role_name]
            for permission_name in permission_names:
                permission = permissions[permission_name]
                if permission not in role.permissions:
                    role.permissions.append(permission)

        role = roles["Administrator"]
        user = User(
            email=email,
            full_name=args.name.strip(),
            password_hash=hash_password(password),
            is_active=True,
            is_superuser=True,
            roles=[role],
        )
        db.add(user)
        db.commit()
        print(f"Administrator created: {email} ({user.id})")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
