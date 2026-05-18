"""
pytest configuration and shared fixtures.
"""

import os
import sys

# Add retriever directory to path so tests can import retriever modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "retriever"))
