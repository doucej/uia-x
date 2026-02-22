"""
Shared pytest configuration.

Makes the workspace root importable without installation so that
``pytest tests/`` works from the project root.
"""

import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
