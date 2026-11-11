"""Miller Columns package.

The Nemo extension and later UI import the version and the metadata
key constants from here. Directory listing, view preference, and clipboard
behavior live in ``miller.data``; the stylesheet lives in ``miller.theme``.

This module stays thin on purpose. Importing the package must not build
widgets or touch the user's view settings.
"""

from .data import (
    METADATA_KEY,
    RESERVED_VIEW_KEY,
    WIDTH_METADATA_KEY,
    resolve_column_width,
    set_column_width,
)

__version__ = "1.0.0"

__all__ = [
    "METADATA_KEY",
    "RESERVED_VIEW_KEY",
    "WIDTH_METADATA_KEY",
    "__version__",
    "resolve_column_width",
    "set_column_width",
]
