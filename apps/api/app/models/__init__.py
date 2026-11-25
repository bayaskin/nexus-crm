# Import every model here so SQLAlchemy metadata and Alembic can discover them.
from app.models.audit import AuditLog
from app.models.catalog import Product
from app.models.crm import Contact, Customer
from app.models.identity import Permission, Role, User, UserPermissionOverride
from app.models.github import GitHubPRRevision, GitHubPullRequestState, GitHubWebhookDelivery
from app.models.inventory import InventoryBalance, InventoryMovement, Warehouse
from app.models.qa import QARun
from app.models.qa_execution import QAExecutionAdmission, QAExecutionCapacity
from app.models.sales import Order, OrderItem
from app.models.sessions import UserSession

__all__ = [
    "AuditLog", "Contact", "Customer", "GitHubPRRevision", "GitHubPullRequestState", "GitHubWebhookDelivery",
    "InventoryBalance", "InventoryMovement", "Order", "OrderItem", "Permission", "Product", "QARun",
    "QAExecutionAdmission", "QAExecutionCapacity", "Role",
    "User", "UserPermissionOverride", "UserSession", "Warehouse",
]
