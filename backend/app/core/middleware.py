import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import SessionLocal
from app.core.models import RequestLog

logger = logging.getLogger("app.requests")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        error_class: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error_class = exc.__class__.__name__
            raise
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            current_user = getattr(request.state, "current_user", None)

            def _attr(obj: object, name: str) -> str | None:
                # Observability must never crash a request. A committed-then-
                # expired ORM instance read here raises DetachedInstanceError
                # (not AttributeError), which getattr's default won't catch.
                try:
                    return getattr(obj, name, None)
                except Exception:
                    return None

            user_id = _attr(current_user, "id")
            org_id = _attr(current_user, "org_id")
            logger.info(
                "request.completed",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "org_id": org_id,
                    "method": request.method,
                    "route": str(request.url.path),
                    "status_code": status_code,
                    "latency_ms": round(latency_ms, 2),
                    "error_class": error_class,
                },
            )
            db = SessionLocal()
            try:
                db.add(
                    RequestLog(
                        request_id=request_id,
                        user_id=user_id,
                        org_id=org_id,
                        method=request.method,
                        route=str(request.url.path),
                        status_code=status_code,
                        latency_ms=latency_ms,
                        error_class=error_class,
                        request_metadata={"query": str(request.url.query) if request.url.query else None},
                    )
                )
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
            if "response" in locals():
                response.headers["X-Request-ID"] = request_id
