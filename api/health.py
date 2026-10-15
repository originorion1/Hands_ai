"""Vercel file-based health function."""

from deployment.vercel.status import StatusHandler


class handler(StatusHandler):
    route = "/api/health"
