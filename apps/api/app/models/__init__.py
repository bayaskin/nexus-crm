# Import every model here so SQLAlchemy metadata and Alembic can discover them.
from app.models.audit import AuditLog
from app.models.catalog import Product
from app.models.crm import Contact, Customer
from app.models.identity import Permission, Role, User
from app.models.inventory import InventoryMovement
from app.models.sales import Order, OrderItem

__all__ = ["AuditLog", "Contact", "Customer", "InventoryMovement", "Order", "OrderItem", "Permission", "Product", "Role", "User"]
