from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.api.routes.customers import router as customers_router
from app.api.routes.products import router as products_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.orders import router as orders_router
from app.core.config import settings
from app.core.database import check_database

app = FastAPI(
    title="Nexus CRM API",
    version="0.2.0",
    description="CRM, order management, inventory, authentication and RBAC platform",
    root_path="/api",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(customers_router)
app.include_router(products_router)
app.include_router(inventory_router)
app.include_router(orders_router)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "nexus-api"}


@app.get("/health/ready", tags=["operations"])
def readiness() -> dict[str, str]:
    try:
        check_database()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from exc

    return {"status": "ready", "database": "connected"}


@app.get("/v1", tags=["operations"])
def api_root() -> dict[str, str]:
    return {
        "name": "Nexus CRM API",
        "version": "v1",
        "status": "development",
    }
