"""API routers."""

from .agent import router as agent_router
from .health import router as health_router
from .lab import router as lab_router
from .market import router as market_router
from .portfolio import router as portfolio_router
from .pwa import router as pwa_router
from .reports import router as reports_router
from .settings import router as settings_router
from .theses import router as theses_router

ROUTERS = [health_router, market_router, theses_router, lab_router, portfolio_router, reports_router, settings_router,
           agent_router, pwa_router]
