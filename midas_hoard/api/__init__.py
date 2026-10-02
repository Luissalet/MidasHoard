"""API routers."""

from .agent import router as agent_router
from .lab import router as lab_router
from .market import router as market_router
from .portfolio import router as portfolio_router
from .reports import router as reports_router
from .settings import router as settings_router
from .status import router as status_router
from .theses import router as theses_router

ROUTERS = [status_router, market_router, theses_router, lab_router, portfolio_router, reports_router, settings_router,
           agent_router]
