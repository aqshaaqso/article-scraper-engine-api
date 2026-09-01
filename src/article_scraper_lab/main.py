"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import __version__
from .dependencies import get_job_manager
from .errors import ExtractionError, FetchError, JobNotFoundError, RobotsDeniedError, UnsafeUrlError
from .routes import job_router, router, system_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    manager = get_job_manager()
    manager.start()
    yield
    manager.shutdown()


app = FastAPI(
    title="Article Scraper Engine API",
    summary="Engine ekstraksi artikel berita melalui API.",
    description=(
        "URL divalidasi saat diterima, saat koneksi dibuka, dan pada setiap redirect. "
        "Akses localhost, jaringan privat, metadata cloud, dan URL ber-credential ditolak."
    ),
    version=__version__,
    docs_url=None,
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.get("/docs", include_in_schema=False)
def docs_redirect() -> RedirectResponse:
    return RedirectResponse("/swagger/index.html")


@app.get("/swagger/index.html", response_class=HTMLResponse, include_in_schema=False)
def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "detail": detail})


@app.exception_handler(UnsafeUrlError)
def handle_unsafe_url(_request: Request, error: UnsafeUrlError) -> JSONResponse:
    return _error(422, "unsafe_url", str(error))


@app.exception_handler(RobotsDeniedError)
def handle_robots_denied(_request: Request, error: RobotsDeniedError) -> JSONResponse:
    return _error(403, "robots_denied", str(error))


@app.exception_handler(FetchError)
def handle_fetch_error(_request: Request, error: FetchError) -> JSONResponse:
    return _error(502, "fetch_failed", str(error))


@app.exception_handler(ExtractionError)
def handle_extraction_error(_request: Request, error: ExtractionError) -> JSONResponse:
    return _error(422, "extraction_failed", str(error))


@app.exception_handler(JobNotFoundError)
def handle_job_not_found(_request: Request, error: JobNotFoundError) -> JSONResponse:
    return _error(404, "job_not_found", str(error))


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/swagger/index.html")


app.include_router(system_router)
app.include_router(router)
app.include_router(job_router)
