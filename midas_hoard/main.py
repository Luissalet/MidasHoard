"""FastAPI application factory: request guard, API routers, static SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import SERVICE, __version__
from .api import ROUTERS
from .config import Config
from .hoard_link import family
from .hoard_link.agentkit import format_issues, issues_of
from .hoard_link.guard import install_guard
from .hoard_link.service import health_router, install_error_handlers, install_pwa, install_spa
from .services import Services

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: Config | None = None, services: Services | None = None) -> FastAPI:
    """``services`` lets tests inject a pre-built instance (fake transport, fake link)."""
    config = config or (services.config if services else Config.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or Services(config)
        app.state.services = svc
        svc.start()
        logging.getLogger("midas").info("Midas's Hoard %s - data in %s", __version__, config.data_dir)
        try:
            yield
        finally:
            svc.stop()

    app = FastAPI(title="Midas's Hoard", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config
    family.configure("midas", str(config.data_dir), token_file=str(config.token_path))

    install_guard(app, port_getter=lambda: config.port, allowed_env="MIDAS_ALLOWED_HOSTS", allowed_hosts=config.allowed_hosts)
    install_error_handlers(app)  # MidasError is an AppError: {"error", "code", "hint"?, ...details} with its own status

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):  # a bad value in a handler (pydantic's ValidationError is one) is the caller's fault
        body = {"error": format_issues(exc), "code": "invalid_arguments", "issues": issues_of(exc)} if callable(getattr(exc, "errors", None)) \
            else {"error": str(exc), "code": "invalid"}
        return JSONResponse(body, status_code=400)

    def health_extra(request: Request) -> dict:
        svc = getattr(request.app.state, "services", None)
        return {"dataDirConfigured": config.data_dir_configured, "offline": svc.http.offline if svc else config.offline,
                "counts": svc.counts() if svc else {}}

    app.include_router(health_router(SERVICE, __version__, extra=health_extra))
    for router in ROUTERS:
        app.include_router(router)

    install_pwa(app, name="Midas's Hoard", short_name="Midas", theme="#5c4400", background="#1a1404", cache="midas-hoard-assets", lang="es",
                static_dir=STATIC_DIR, version=__version__)
    install_spa(app, STATIC_DIR)  # last: everything that is not an API route or a real file is the single page app
    return app
