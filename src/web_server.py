"""Web server for exposing tag compliance metrics.

Runs as a long-lived service that periodically scans AWS resources
and exposes Prometheus metrics via HTTP endpoint.
"""
import asyncio
import logging
import signal
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

from src.aws_audit import validate_resource_tags
from src.metrics import update_metrics, expose_prometheus_metrics

logger = logging.getLogger(__name__)

# Global state
app = FastAPI(title="AWS Tag Compliance Exporter", version="1.0.0")
_last_scan_time: Optional[datetime] = None
_last_scan_error: Optional[str] = None
_scan_in_progress: bool = False
_shutdown_event = asyncio.Event()


class MetricsRefreshManager:
    """Manages background metrics refresh task."""

    def __init__(
        self,
        config: dict,
        refresh_interval_seconds: int = 300,
    ):
        self.config = config
        self.refresh_interval = refresh_interval_seconds
        self.task: Optional[asyncio.Task] = None

    def start(self):
        """Start background refresh task."""
        if self.task is None:
            self.task = asyncio.create_task(self._refresh_loop())
            logger.info("Metrics refresh task started (interval: %ds)", self.refresh_interval)

    async def stop(self):
        """Stop background refresh task."""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            logger.info("Metrics refresh task stopped")

    async def _refresh_loop(self):
        """Background loop to periodically refresh metrics."""
        global _last_scan_time, _last_scan_error, _scan_in_progress

        # Run initial scan immediately
        await self._run_scan()

        while not _shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=self.refresh_interval
                )
                break  # Shutdown event was set
            except asyncio.TimeoutError:
                # Timeout means we should run the scan
                await self._run_scan()

    async def _run_scan(self):
        """Execute AWS scan and update metrics."""
        global _last_scan_time, _last_scan_error, _scan_in_progress

        if _scan_in_progress:
            logger.warning("Scan already in progress, skipping this cycle")
            return

        _scan_in_progress = True
        scan_start = datetime.now()

        try:
            logger.info("Starting AWS resource scan")

            # Extract config
            matrix = self.config.get('aws_account_matrix', [])
            required_tags = self.config.get('REQUIRED_TAGS', [])
            assume_template = self.config.get('assume_role_name_template')
            overrides = self.config.get('aws_account_overrides', {})
            excluded_types = self.config.get('excluded_resource_types', [])

            # Run scan in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                validate_resource_tags,
                matrix,
                required_tags,
                assume_template,
                overrides,
                excluded_types
            )

            # Update metrics
            update_metrics(results)

            _last_scan_time = datetime.now()
            _last_scan_error = None

            scan_duration = (datetime.now() - scan_start).total_seconds()
            logger.info(
                "Scan completed successfully in %.2fs. Next scan in %ds",
                scan_duration,
                self.refresh_interval
            )

        except Exception as e:
            _last_scan_error = str(e)
            logger.error("Scan failed: %s", e, exc_info=True)

        finally:
            _scan_in_progress = False


# Global refresh manager (initialized in startup)
_refresh_manager: Optional[MetricsRefreshManager] = None


@app.on_event("startup")
async def startup_event():
    """Initialize background refresh task on startup."""
    logger.info("Application starting up")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown of background tasks."""
    global _refresh_manager
    logger.info("Application shutting down")
    _shutdown_event.set()

    if _refresh_manager:
        await _refresh_manager.stop()


@app.get("/metrics", response_class=Response)
async def metrics_endpoint():
    """Prometheus metrics endpoint.

    Returns current tag compliance metrics in Prometheus exposition format.
    This endpoint is scraped by Prometheus.
    """
    return expose_prometheus_metrics()


@app.get("/health", response_class=PlainTextResponse)
async def health_endpoint():
    """Kubernetes health check endpoint.

    Returns:
        200 OK if service is healthy
        503 Service Unavailable if last scan failed
    """
    global _last_scan_time, _last_scan_error

    if _last_scan_error:
        return Response(
            content=f"Unhealthy: Last scan failed - {_last_scan_error}",
            status_code=503,
            media_type="text/plain"
        )

    if _last_scan_time:
        uptime = (datetime.now() - _last_scan_time).total_seconds()
        return PlainTextResponse(
            f"OK - Last scan: {_last_scan_time.isoformat()} ({uptime:.0f}s ago)"
        )

    return PlainTextResponse("OK - Initializing")


@app.get("/ready", response_class=PlainTextResponse)
async def readiness_endpoint():
    """Kubernetes readiness check endpoint.

    Returns:
        200 OK if at least one successful scan has completed
        503 Service Unavailable if no successful scan yet
    """
    global _last_scan_time

    if _last_scan_time is None:
        return Response(
            content="Not ready: No successful scan yet",
            status_code=503,
            media_type="text/plain"
        )

    return PlainTextResponse("Ready")


@app.get("/", response_class=PlainTextResponse)
async def root_endpoint():
    """Root endpoint with service information."""
    global _last_scan_time, _last_scan_error, _scan_in_progress

    status_lines = [
        "AWS Tag Compliance Exporter - Web Mode",
        "=" * 50,
        "",
        "Endpoints:",
        "  GET /metrics  - Prometheus metrics",
        "  GET /health   - Health check (liveness)",
        "  GET /ready    - Readiness check",
        "",
        "Status:",
    ]

    if _scan_in_progress:
        status_lines.append("  Scan: IN PROGRESS")
    elif _last_scan_time:
        uptime = (datetime.now() - _last_scan_time).total_seconds()
        status_lines.append(f"  Last scan: {_last_scan_time.isoformat()} ({uptime:.0f}s ago)")
    else:
        status_lines.append("  Last scan: Never")

    if _last_scan_error:
        status_lines.append(f"  Last error: {_last_scan_error}")
    else:
        status_lines.append("  Status: OK")

    return PlainTextResponse("\n".join(status_lines))


def run_web_server(
    config: dict,
    host: str = "0.0.0.0",
    port: int = 8080,
    refresh_interval: int = 300
):
    """Run FastAPI web server with background metrics refresh.

    Args:
        config: Configuration dict from config.yaml
        host: Host to bind to (default: 0.0.0.0 for container compatibility)
        port: Port to listen on (default: 8080)
        refresh_interval: Seconds between metric refreshes (default: 300)
    """
    global _refresh_manager

    import uvicorn

    # Initialize refresh manager
    _refresh_manager = MetricsRefreshManager(config, refresh_interval)

    # Setup signal handlers for graceful shutdown
    def handle_shutdown(signum, frame):
        logger.info("Received shutdown signal")
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Start background refresh task
    async def startup():
        _refresh_manager.start()

    app.add_event_handler("startup", startup)

    # Run server
    logger.info("Starting web server on %s:%d", host, port)
    logger.info("Metrics refresh interval: %ds", refresh_interval)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
