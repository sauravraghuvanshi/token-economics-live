"""Send only billing and quality metadata to Azure Monitor."""

import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from .settings import Settings

LOG = logging.getLogger("tokecon")
_configured = False


def setup(settings: Settings) -> None:
    global _configured
    connection_string = settings.applicationinsights_connection_string.get_secret_value()
    if _configured or not connection_string or settings.source != "live":
        return
    from azure.identity import DefaultAzureCredential
    from azure.monitor.opentelemetry import configure_azure_monitor

    kwargs: dict[str, Any] = {}
    if os.getenv("WEBSITE_SITE_NAME"):
        kwargs["credential"] = DefaultAzureCredential()
    configure_azure_monitor(
        connection_string=connection_string, logger_name="tokecon", enable_live_metrics=False,
        sampling_ratio=1.0,
        instrumentation_options={name: {"enabled": False} for name in
                                 ("azure_sdk", "django", "fastapi", "flask", "psycopg2",
                                  "requests", "urllib", "urllib3")}, **kwargs,
    )
    _configured = True


def model_span(attributes: dict[str, Any], start: int, end: int) -> None:
    span = trace.get_tracer("tokecon.foundry").start_span(
        f"Foundry {attributes['kind']}", kind=SpanKind.CLIENT, start_time=start,
        attributes={k: v for k, v in attributes.items() if v is not None},
    )
    if attributes.get("error"):
        span.set_status(Status(StatusCode.ERROR))
    span.end(end_time=end)


def completed(fields: dict[str, Any]) -> None:
    LOG.info("run_completed", extra={"microsoft.custom_event.name": "run_completed", **fields})
