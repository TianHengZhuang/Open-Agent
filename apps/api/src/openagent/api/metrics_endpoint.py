"""Prometheus scrape endpoint (Phase 12).

``GET /metrics`` renders workflow operations in the exposition format. It is
not an org endpoint: a scrape is machine-to-machine. It is therefore disabled
unless ``OPENAGENT_METRICS_TOKEN`` is configured (or the app runs in
development), and a configured token must be presented as
``Authorization: Bearer <token>`` or ``?token=<token>``.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, Response, status

from openagent.core.config import get_settings
from openagent.core.metrics import metrics as request_metrics
from openagent.runtime.prometheus import render_extra, render_workflow_metrics

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def metrics_token() -> str:
    return os.environ.get("OPENAGENT_METRICS_TOKEN", "").strip()


def _presented_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return (request.query_params.get("token") or "").strip()


async def metrics_response(request: Request) -> Response:
    token = metrics_token()
    if token:
        if not hmac.compare_digest(_presented_token(request), token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Invalid metrics token", "code": "METRICS_TOKEN_INVALID"},
            )
    elif not get_settings().is_development:
        # Never expose run counts to an anonymous scrape in production.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    from openagent.db import session as db_session
    from openagent.runtime.workflow_ops import execution_metrics

    maker = db_session.async_session_maker
    if maker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "Database is not ready", "code": "METRICS_UNAVAILABLE"},
        )
    async with maker() as session:
        snapshot = await execution_metrics(session, None)

    body = render_workflow_metrics(snapshot) + render_extra([request_metrics.to_prometheus()])
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
