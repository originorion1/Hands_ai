"""Vercel report API route: explicitly disconnected until Stage B is qualified."""

from deployment.vercel.report import ReportHandler


class handler(ReportHandler):
    pass
