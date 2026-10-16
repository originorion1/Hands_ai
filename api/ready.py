"""Vercel report-host readiness route."""

from deployment.vercel.readiness import ReadinessHandler


class handler(ReadinessHandler):
    pass
