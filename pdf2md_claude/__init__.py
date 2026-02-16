"""Claude native PDF -> Markdown conversion package."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("pdf2md-claude")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for uninstalled dev usage
