"""Vendor-neutral connector metadata; registration grants no runtime authority."""

from .registry import CONTRACT_VERSION, ConnectorRegistration, ReviewedConnector, discover

__all__ = ["CONTRACT_VERSION", "ConnectorRegistration", "ReviewedConnector", "discover"]
