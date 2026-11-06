"""Miller Columns package.

The Nemo extension and later UI import the version and the two path
constants from here. Directory listing, view preference, and clipboard
behavior live in ``miller.data``; the stylesheet lives in ``miller.theme``.

This module stays thin on purpose. Importing the package must not build
widgets or touch the user's view settings.
"""

from .data import DASHBOARD_PATH, METADATA_KEY

__version__ = "0.1.0"

__all__ = ["DASHBOARD_PATH", "METADATA_KEY", "__version__"]
