"""General local-first browser playgrounds for olab_code packages."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("olab-playground")
except PackageNotFoundError:
    __version__ = "0.0.0"
