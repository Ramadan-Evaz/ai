
# This file marks the directory as a Python package.
from .config import Settings, get_settings

# Lazy Import to Avoid Circular Import
import importlib

def get_logger():
    return importlib.import_module(".evazan_ai_logger", package="ansari")

__all__ = ["Settings", "get_settings", "get_logger"]
