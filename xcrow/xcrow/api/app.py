"""FastAPI admin API + web dashboard for Xcrow."""
from fastapi import FastAPI, Security, HTTPException
from fastapi.security.api_key import APIKeyHeader
from starlette.middleware.sessions import SessionMiddleware
from config import settings

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="Xcrow Admin API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url=None,
)

# Session middleware for web dashboard login
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.DASHBOARD_SESSION_SECRET,
    session_cookie="xcrow_session",
    max_age=86400,   # 24 hours
    https_only=False,
)


async def verify_api_key(key: str = Security(API_KEY_HEADER)) -> str:
    if key != settings.API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "bot": settings.BOT_USERNAME}


# ── JSON API routers ────────────────────────────────────────────────────────
from api.routers import deals, admin as admin_router
app.include_router(deals.router,        prefix="/deals",  tags=["Deals"])
app.include_router(admin_router.router, prefix="/admin",  tags=["Admin"])

# ── Web admin dashboard ─────────────────────────────────────────────────────
from api.dashboard import dashboard_router
app.include_router(dashboard_router, prefix="/panel")
