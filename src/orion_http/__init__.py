"""Installed HTTP adapters kept outside the ORION kernel."""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
