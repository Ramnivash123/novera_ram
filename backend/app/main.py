"""
Main FastAPI application entry point.
Configures routes, middleware, and application lifecycle events.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi import HTTPException
from loguru import logger
import sys
from pathlib import Path

from app.core.config import settings
from app.db.session import init_db, close_db
from app.api.endpoints import (
    health, documents, auth, chat, search,
    admin, document_editor, customization, organization
)


# ============================================
# LOGGING CONFIGURATION
# ============================================
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=settings.log_level,
    colorize=True
)
logger.add(
    settings.log_file,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level=settings.log_level,
    rotation=settings.log_rotation,
    retention=settings.log_retention,
    compression="zip"
)


# ============================================
# APPLICATION LIFESPAN
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("🚀 Starting Novera AI Knowledge Assistant...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"CORS Origins: {settings.cors_origins_list}")

    try:
        await init_db()
        logger.info("✅ Database initialized")

        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(settings.upload_dir + "/branding").mkdir(parents=True, exist_ok=True)
        logger.info("✅ Upload directories created")

        logger.info("📦 Pre-loading embedding model...")
        try:
            from app.services.embedding.embedding_service import embedding_service
            embedding_service.use_local_fallback = True
            embedding_service._init_local_model()
            logger.info("✅ Embedding model pre-loaded")
        except Exception as e:
            logger.warning(f"⚠️ Model pre-load failed: {e}")

        logger.info("🎉 Application startup complete!")

    except Exception as e:
        logger.error(f"❌ Startup failed: {str(e)}")
        raise

    yield

    # Shutdown
    logger.info("🛑 Shutting down Novera AI Knowledge Assistant...")
    try:
        await close_db()
        logger.info("✅ Database connections closed")
        logger.info("👋 Shutdown complete")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {str(e)}")


# ============================================
# CREATE APPLICATION
# ============================================
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-powered knowledge assistant with RAG capabilities",
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
    openapi_url="/api/openapi.json" if settings.debug else None,
    lifespan=lifespan
)


# ============================================
# RESOLVE FRONTEND PATH (works in Docker + Local)
# ============================================
_possible_frontend_paths = [
    Path(__file__).resolve().parent.parent / "frontend" / "dist",   # /app/frontend/dist (Docker)
    Path(__file__).resolve().parents[2] / "frontend" / "dist",      # Local dev (MENTANOVA/frontend/dist)
    Path("/app/frontend/dist"),                                      # Absolute Docker path
]

frontend_path = None
for _path in _possible_frontend_paths:
    if _path.exists() and (_path / "index.html").exists():
        frontend_path = _path
        break

if frontend_path is None:
    frontend_path = _possible_frontend_paths[0]  # Default fallback
    logger.warning(f"⚠️ Frontend build not found. Tried: {[str(p) for p in _possible_frontend_paths]}")
else:
    logger.info(f"✅ Frontend build found at: {frontend_path}")

logger.info(f"Frontend path: {frontend_path} (exists: {frontend_path.exists()})")


# ============================================
# MIDDLEWARE (ORDER MATTERS — CORS FIRST)
# ============================================
logger.info(f"🔐 Configuring CORS with origins: {settings.cors_origins_list}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

logger.info("✅ Middleware configured")


# ============================================
# API ROUTERS — REGISTERED FIRST (BEFORE CATCH-ALL)
# ============================================
app.include_router(
    health.router,
    prefix=settings.api_v1_prefix,
    tags=["Health"]
)

app.include_router(
    auth.router,
    prefix=settings.api_v1_prefix,
    tags=["Authentication"]
)

app.include_router(
    documents.router,
    prefix=settings.api_v1_prefix,
    tags=["Documents"]
)

app.include_router(
    search.router,
    prefix=settings.api_v1_prefix,
    tags=["Search"]
)

app.include_router(
    chat.router,
    prefix=settings.api_v1_prefix,
    tags=["Chat"]
)

app.include_router(
    customization.router,
    prefix=settings.api_v1_prefix,
    tags=["Customization"]
)

app.include_router(
    organization.router,
    prefix=settings.api_v1_prefix,
    tags=["Super Admin - Organizations"]
)

app.include_router(
    admin.router,
    prefix=settings.api_v1_prefix,
    tags=["Admin"]
)

app.include_router(
    document_editor.router,
    prefix=settings.api_v1_prefix,
    tags=["Document Editor"]
)


# ============================================
# STATIC FILE MOUNTS (AFTER API ROUTERS)
# ============================================
upload_path = Path(settings.upload_dir)
upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")
logger.info(f"✅ Static files mounted at /uploads -> {upload_path}")

if frontend_path and frontend_path.exists() and (frontend_path / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_path / "assets")), name="frontend_assets")
    logger.info(f"✅ Frontend assets mounted from {frontend_path / 'assets'}")


# ============================================
# DEBUG: PRINT ALL REGISTERED ROUTES
# ============================================
print("\n==== REGISTERED ROUTES ====")
for route in app.routes:
    try:
        if hasattr(route, 'methods') and route.methods:
            methods = ", ".join(sorted(route.methods))
        else:
            methods = "MOUNT"
        print(f"  {methods:30s} {route.path}")
    except Exception:
        pass
print("==== END ROUTES ====\n")


# ============================================
# HEALTH CHECK & ROOT (for Render)
# ============================================
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root_handler(request: Request):
    """
    Root endpoint:
    - HEAD: Returns 200 for Render health checks
    - GET: Serves frontend index.html if available, otherwise JSON health response
    """
    if request.method == "HEAD":
        return JSONResponse({"status": "ok"})

    # Try serving frontend
    if frontend_path and frontend_path.exists():
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(index_file)

    # No frontend? Return API health response
    return JSONResponse({
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "frontend_available": frontend_path.exists() if frontend_path else False,
        "docs": "/api/docs" if settings.debug else None
    })


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon from frontend build."""
    if frontend_path and frontend_path.exists():
        favicon_file = frontend_path / "favicon.ico"
        if favicon_file.exists():
            return FileResponse(favicon_file)
    raise HTTPException(status_code=404)


# ============================================
# SPA FALLBACK — MUST BE THE ABSOLUTE LAST ROUTE
# ============================================
@app.api_route("/{full_path:path}", methods=["GET"], include_in_schema=False)
async def serve_spa_fallback(request: Request, full_path: str):
    """
    SPA fallback: serves index.html for all frontend routes.
    
    CRITICAL RULES:
    1. This MUST be the last route registered
    2. Only handles GET (so POST/PUT/DELETE to API routes work correctly)
    3. Never intercepts /api/* or /uploads/* paths
    """
    # ---- NEVER intercept API routes ----
    if full_path.startswith("api"):
        raise HTTPException(
            status_code=404,
            detail=f"API endpoint not found: /{full_path}"
        )

    # ---- NEVER intercept upload routes ----
    if full_path.startswith("uploads"):
        raise HTTPException(status_code=404, detail="File not found")

    # ---- NEVER intercept asset routes (handled by StaticFiles mount) ----
    if full_path.startswith("assets"):
        raise HTTPException(status_code=404, detail="Asset not found")

    # ---- Try serving actual static file from frontend build ----
    if frontend_path and frontend_path.exists():
        static_file = frontend_path / full_path
        try:
            # Security: prevent path traversal attacks
            static_file.resolve().relative_to(frontend_path.resolve())
            if static_file.exists() and static_file.is_file():
                return FileResponse(static_file)
        except ValueError:
            pass  # Path traversal attempt, ignore

    # ---- SPA: serve index.html for client-side routing ----
    if frontend_path and frontend_path.exists():
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(index_file)

    # ---- No frontend build available ----
    raise HTTPException(status_code=404, detail="Not Found")


# ============================================
# EXCEPTION HANDLERS
# ============================================
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions."""
    logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "message": exc.detail,
            "status_code": exc.status_code
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors."""
    logger.error(f"Validation error on {request.method} {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "message": "Invalid request data",
            "details": exc.errors() if settings.debug else None
        }
    )


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    """Handle database integrity errors."""
    logger.error(f"Integrity error on {request.method} {request.url.path}: {str(exc)}")
    error_msg = str(exc.orig) if hasattr(exc, 'orig') else str(exc)

    if "duplicate key" in error_msg.lower() or "unique constraint" in error_msg.lower():
        return JSONResponse(
            status_code=409,
            content={
                "error": "Conflict",
                "message": "A record with this information already exists",
                "type": "IntegrityError"
            }
        )

    return JSONResponse(
        status_code=400,
        content={
            "error": "Invalid data",
            "message": "The provided data violates database constraints",
            "type": "IntegrityError"
        }
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """Handle SQLAlchemy database errors."""
    logger.error(f"Database error on {request.method} {request.url.path}: {str(exc)}")
    logger.exception("Database error details:", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Database error",
            "message": "A database error occurred" if not settings.debug else str(exc),
            "type": "DatabaseError"
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}")
    logger.error(f"Exception type: {type(exc).__name__}")
    logger.error(f"Exception message: {str(exc)}")
    logger.exception("Full traceback:", exc_info=exc)

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc) if settings.debug else "An unexpected error occurred",
            "type": type(exc).__name__ if settings.debug else None,
            "path": str(request.url.path)
        }
    )


# ============================================
# DIRECT RUN (for local development)
# ============================================
if __name__ == "__main__":
    import uvicorn
    logger.info(f"🚀 Starting server on {settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=1 if settings.debug else settings.workers,
        log_level=settings.log_level.lower()
    )
