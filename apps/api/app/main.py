from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.users import router as users_router
from app.api.routes.customers import router as customers_router
from app.api.routes.products import router as products_router
from app.api.routes.inventory import router as inventory_router
from app.api.routes.orders import router as orders_router
from app.api.routes.qa import router as qa_router
from app.api.routes.github import router as github_router
from app.core.config import settings
from app.core.database import check_database

app = FastAPI(
    title="Nexus CRM API",
    version="0.2.0",
    description="CRM, order management, inventory, authentication and RBAC platform",
    root_path="/api",
)

_SENSITIVE_VALIDATION_FIELDS = {
    "password", "current_password", "new_password", "temporary_password",
    "refresh_token", "access_token", "jwt_secret_key", "token", "authorization",
}


@app.exception_handler(RequestValidationError)
async def validation_error_response(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Do not echo secrets from invalid request fields in 422 responses."""

    def contains_sensitive_field(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).casefold() in _SENSITIVE_VALIDATION_FIELDS or contains_sensitive_field(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_sensitive_field(item) for item in value)
        return False

    safe_errors = []
    for error in exc.errors():
        safe_error = dict(error)
        location = {str(part).casefold() for part in error.get("loc", ())}
        if location & _SENSITIVE_VALIDATION_FIELDS or contains_sensitive_field(error.get("input")):
            safe_error.pop("input", None)
        safe_errors.append(safe_error)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(safe_errors)})

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
app.include_router(qa_router)
app.include_router(github_router)


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
