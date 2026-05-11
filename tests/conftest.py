"""
pytest configuration and shared fixtures.
"""

import sys
import os

# Add retriever directory to path so tests can import retriever modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "retriever"))
