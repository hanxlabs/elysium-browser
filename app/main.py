"""Elysium 浏览器网关 HTTP 入口。"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.browser import CloakBrowserFetcher
from app.config import Settings, get_settings
from app.models import (
    ErrorResponse,
    FetchPageRequest,
    FetchPageResponse,
    SiteLoginRequest,
    SiteLoginResponse,
)
from app.security import require_gateway_token
from app.site_login import SiteLoginService

logger = logging.getLogger("elysium.browser_gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化共享配置、抓取器和并发限制器。"""
    settings = get_settings()
    app.state.settings = settings
    app.state.fetcher = CloakBrowserFetcher(settings)
    app.state.site_login_service = SiteLoginService(settings)
    app.state.semaphore = asyncio.Semaphore(settings.max_concurrency)
    logger.info(
        "浏览器网关启动: max_concurrency=%s",
        settings.max_concurrency,
    )
    yield


app = FastAPI(
    title="Elysium Browser Gateway",
    version="1.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.exception_handler(ValueError)
async def handle_value_error(_: Request, error: ValueError) -> JSONResponse:
    """将受控输入或地址校验失败转换为统一 400 响应。"""
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(error)})


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """返回不包含敏感配置的存活状态。"""
    settings: Settings = request.app.state.settings
    return {
        "status": "ok",
        "tokenConfigured": bool(settings.token),
        "outboundPolicy": "public-http-https-only",
    }


@app.post(
    "/internal/v1/pages/fetch",
    response_model=FetchPageResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def fetch_page(
    payload: FetchPageRequest,
    request: Request,
    _: None = Depends(require_gateway_token),
) -> FetchPageResponse:
    """以隔离浏览器上下文抓取已批准站点的渲染页面。"""
    async with request.app.state.semaphore:
        try:
            return await asyncio.to_thread(request.app.state.fetcher.fetch, payload)
        except ValueError:
            raise
        except Exception as error:
            logger.exception("浏览器页面抓取失败: request_id=%s site_key=%s", payload.request_id, payload.site_key)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="浏览器页面抓取失败") from error


@app.post(
    "/internal/v1/sites/login",
    response_model=SiteLoginResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def login_site(
    payload: SiteLoginRequest,
    request: Request,
    _: None = Depends(require_gateway_token),
) -> SiteLoginResponse:
    """选择站点专属适配器，在隔离浏览器上下文中执行一次登录。"""
    async with request.app.state.semaphore:
        return await asyncio.to_thread(request.app.state.site_login_service.login, payload)
