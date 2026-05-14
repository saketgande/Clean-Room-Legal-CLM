import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import AIValidationStatus
from app.core.models import AICallLog


class ClaudeClient:
    provider = "claude"

    async def structured_completion(
        self,
        *,
        db: Session,
        org_id: str,
        created_by_user_id: str | None,
        request_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
    ) -> BaseModel:
        started = time.perf_counter()
        raw_output: dict[str, Any] | None = None
        validation_status = AIValidationStatus.NOT_VALIDATED
        error_class = None
        try:
            if settings.mock_claude:
                raw_output = {}
            else:
                raw_output = await self._call_claude(system_prompt=system_prompt, user_prompt=user_prompt)
            try:
                validated = output_model.model_validate(raw_output)
                validation_status = AIValidationStatus.VALID
                return validated
            except ValidationError:
                validation_status = AIValidationStatus.INVALID
                raise
        except Exception as exc:
            error_class = exc.__class__.__name__
            raise
        finally:
            db.add(
                AICallLog(
                    org_id=org_id,
                    request_id=request_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    provider=self.provider,
                    model=settings.claude_model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    validation_status=validation_status,
                    error_class=error_class,
                    raw_ai_output=raw_output,
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )

    async def _call_claude(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY is required when mock Claude mode is disabled")
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.claude_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.claude_model,
                    "max_tokens": 2048,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            response.raise_for_status()
            return response.json()


claude_client = ClaudeClient()
