from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, cards, chat, materials, payments, quizzes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LinguaMind API",
        description="Language learning through content consumption",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "version": "0.1.0"}

    # Include routers
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(materials.router, prefix=settings.api_v1_prefix)
    app.include_router(cards.router, prefix=settings.api_v1_prefix)
    app.include_router(quizzes.router, prefix=settings.api_v1_prefix)
    app.include_router(chat.router, prefix=settings.api_v1_prefix)
    app.include_router(payments.router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
