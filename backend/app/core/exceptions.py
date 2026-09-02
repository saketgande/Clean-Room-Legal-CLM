import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", None)
        response = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": request_id},
            headers=getattr(exc, "headers", None),
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        response = JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            # jsonable_encoder matches FastAPI's own handler and is required,
            # not cosmetic: each error carries the offending `input`, which for
            # a file field is raw bytes. Serialising that directly raises inside
            # the handler, so a malformed upload surfaced as a 500 stack trace
            # instead of the 422 the client needs to fix its request.
            content=jsonable_encoder({"detail": exc.errors(), "request_id": request_id}),
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Unhandled request error",
            extra={
                "request_id": request_id,
                "route": str(request.url.path),
                "method": request.method,
                "error_class": exc.__class__.__name__,
            },
        )
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "request_id": request_id},
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response
