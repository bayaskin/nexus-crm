# Import every model here so SQLAlchemy metadata and Alembic can discover them.
from app.models.audit import AuditLog
from app.models.catalog import Product
from app.models.crm import Contact, Customer
from app.models.identity import Permission, Role, User, UserPermissionOverride
from app.models.inventory import InventoryMovement
from app.models.sales import Order, OrderItem
from app.models.sessions import UserSession

__all__ = [
    "AuditLog", "Contact", "Customer", "InventoryMovement", "Order", "OrderItem",
    "Permission", "Product", "Role", "User", "UserPermissionOverride", "UserSession",
]
