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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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

    # A model call that failed is not an app bug: say what happened, in a plain sentence. Busy or unreachable is
    # 503 (with Retry-After when the provider sent one), a rejected request 502, an answer that came back cut off,
    # refused or unusable 422. These are registered for the AI error types only, not inside the catch-all below:
    # a handler for a specific class runs inside the CORS middleware (the browser can read the reply) and is not
    # replaced by the traceback page that `debug=True` puts in front of the catch-all, and an httpx error from
    # some other service stays the 500 it was.
    async def model_failure_handler(request: Request, exc: Exception):
        from app.ai.outcomes import MODEL_OUTCOMES, Outcome, failure_from_exception

        failure = failure_from_exception(exc)
        if Outcome(failure["outcome"]) not in MODEL_OUTCOMES:
            return _internal_error(request, exc)
        request_id = getattr(request.state, "request_id", None)
        code = {"transient": 503, "permanent": 502}.get(failure["outcome"], status.HTTP_422_UNPROCESSABLE_CONTENT)
        logger.warning(
            "Model call failed", extra={"request_id": request_id, "route": str(request.url.path),
                                        "outcome": failure["outcome"], "error_class": exc.__class__.__name__},
        )
        response = JSONResponse(
            status_code=code,
            content={"detail": failure["reason"], "outcome": failure["outcome"], "request_id": request_id},
            headers={"Retry-After": str(failure["retry_after_s"])} if failure.get("retry_after_s") else None,
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    # Imported here so this module stays importable before the AI package is.
    import anthropic

    from app.ai.gateway.gateway import AIGatewayError

    app.add_exception_handler(anthropic.APIError, model_failure_handler)
    app.add_exception_handler(AIGatewayError, model_failure_handler)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return _internal_error(request, exc)


def _internal_error(request: Request, exc: Exception) -> JSONResponse:
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
