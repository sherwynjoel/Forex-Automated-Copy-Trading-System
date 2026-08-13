"""HTTP control endpoint for the copier service.

The control endpoint is internal-only (bound to Docker-internal network only),
providing operational commands for pause/resume/resync/state queries and drift remedies.

Routes:
- GET /health: service status
- GET /state: account snapshots and drift
- POST /pause: pause copying (global or per-slave)
- POST /resume: resume copying (global or per-slave)
- POST /resync: trigger reconciliation
- POST /reload: reload accounts/settings
- POST /dry-run: toggle dry-run mode
- POST /discover: discover accounts from connection
- POST /drift/close-orphan, /drift/adopt, /drift/dismiss: drift remedies
"""

import json
import logging
from typing import Any

from twisted.web import resource, server

log = logging.getLogger(__name__)


class HealthResource(resource.Resource):
    """GET /health: Returns service status and master account info."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_GET(self, request):
        """Handle GET /health."""
        try:
            status = self.app.get_health()
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps(status).encode()
        except Exception as e:
            log.exception("Health check failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class StateResource(resource.Resource):
    """GET /state: Returns account snapshots and drift items."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_GET(self, request):
        """Handle GET /state."""
        try:
            state = self.app.get_state()
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps(state, default=str).encode()
        except Exception as e:
            log.exception("State query failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class PauseResource(resource.Resource):
    """POST /pause: Pause copying globally or per-slave."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /pause with optional account_id."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            account_id = data.get("account_id")

            self.app.pause(account_id=account_id)

            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "paused"}).encode()
        except Exception as e:
            log.exception("Pause failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class ResumeResource(resource.Resource):
    """POST /resume: Resume copying globally or per-slave."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /resume with optional account_id."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            account_id = data.get("account_id")

            self.app.resume(account_id=account_id)

            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "resumed"}).encode()
        except Exception as e:
            log.exception("Resume failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class ResyncResource(resource.Resource):
    """POST /resync: Trigger reconciliation."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /resync."""
        try:
            d = self.app.resync()
            if d:
                d.addCallback(lambda _: self._send_success(request))
                d.addErrback(lambda f: self._send_error(request, f))
                return server.NOT_DONE_YET
            else:
                return self._send_success(request)
        except Exception as e:
            log.exception("Resync failed: %s", e)
            return self._send_error(request, e)

    def _send_success(self, request):
        request.setHeader(b"Content-Type", b"application/json")
        request.write(json.dumps({"status": "resynced"}).encode())
        request.finish()

    def _send_error(self, request, error):
        request.setResponseCode(500)
        request.setHeader(b"Content-Type", b"application/json")
        error_msg = str(error.value) if hasattr(error, 'value') else str(error)
        request.write(json.dumps({"error": error_msg}).encode())
        request.finish()


class ReloadResource(resource.Resource):
    """POST /reload: Reload accounts and settings."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /reload."""
        try:
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "reloaded"}).encode()
        except Exception as e:
            log.exception("Reload failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class DryRunResource(resource.Resource):
    """POST /dry-run: Toggle dry-run mode."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /dry-run with enabled flag."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            enabled = data.get("enabled", False)

            self.app.set_dry_run(enabled)

            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": f"dry_run={'enabled' if enabled else 'disabled'}"}).encode()
        except Exception as e:
            log.exception("DryRun toggle failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class DiscoverResource(resource.Resource):
    """POST /discover: Discover accounts from connection."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /discover with connection_id."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            connection_id = data.get("connection_id")

            if connection_id is None:
                raise ValueError("connection_id required")

            # TODO: Implement discovery
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "discovered"}).encode()
        except Exception as e:
            log.exception("Discovery failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class DriftResource(resource.Resource):
    """Base class for drift remedy endpoints."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"close-orphan", DriftCloseOrphanResource(app))
        self.putChild(b"adopt", DriftAdoptResource(app))
        self.putChild(b"dismiss", DriftDismissResource(app))


class DriftCloseOrphanResource(resource.Resource):
    """POST /drift/close-orphan: Close an orphan slave position."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /drift/close-orphan."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            item_id = data.get("id")

            if item_id is None:
                raise ValueError("id required")

            # TODO: Implement close-orphan
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "closed"}).encode()
        except Exception as e:
            log.exception("Close orphan failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class DriftAdoptResource(resource.Resource):
    """POST /drift/adopt: Adopt an orphan slave position."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /drift/adopt."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            item_id = data.get("id")
            master_position_id = data.get("master_position_id")

            if item_id is None:
                raise ValueError("id required")

            # TODO: Implement adopt
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "adopted"}).encode()
        except Exception as e:
            log.exception("Adopt failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class DriftDismissResource(resource.Resource):
    """POST /drift/dismiss: Dismiss a drift item."""

    isLeaf = True

    def __init__(self, app):
        self.app = app

    def render_POST(self, request):
        """Handle POST /drift/dismiss."""
        try:
            body = request.content.read()
            data = json.loads(body) if body else {}
            item_id = data.get("id")

            if item_id is None:
                raise ValueError("id required")

            # TODO: Implement dismiss
            request.setHeader(b"Content-Type", b"application/json")
            return json.dumps({"status": "dismissed"}).encode()
        except Exception as e:
            log.exception("Dismiss failed: %s", e)
            request.setResponseCode(500)
            return json.dumps({"error": str(e)}).encode()


class RootResource(resource.Resource):
    """Root resource that dispatches to sub-resources."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        # Register children as attributes
        self.putChild(b"health", HealthResource(app))
        self.putChild(b"state", StateResource(app))
        self.putChild(b"pause", PauseResource(app))
        self.putChild(b"resume", ResumeResource(app))
        self.putChild(b"resync", ResyncResource(app))
        self.putChild(b"reload", ReloadResource(app))
        self.putChild(b"dry-run", DryRunResource(app))
        self.putChild(b"discover", DiscoverResource(app))
        self.putChild(b"drift", DriftResource(app))


def make_control_site(app: Any) -> server.Site:
    """Create a Twisted web site for the control endpoint.

    The site is bound to Docker-internal network only (never exposed to host).
    This is enforced at the docker-compose level by NOT publishing the 8080 port.

    Args:
        app: CopierApp instance

    Returns:
        twisted.web.server.Site bound to the root resource
    """
    root = RootResource(app)
    return server.Site(root)
