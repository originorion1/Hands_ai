"""Vercel file-based status function."""

from deployment.vercel.status import StatusHandler


class handler(StatusHandler):
    route = "/api/v1/status"
