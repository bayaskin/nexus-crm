from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import check_database

app = FastAPI(
    title="Nexus CRM API",
    version="0.1.0",
    description="CRM, order management, and inventory platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "nexus-api"}


@app.get("/health/ready", tags=["operations"])
def readiness() -> dict[str, str]:
    try:
        check_database()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ready", "database": "connected"}


@app.get("/api/v1", tags=["operations"])
def api_root() -> dict[str, str]:
    return {"name": "Nexus CRM API", "version": "v1", "status": "development"}
