"""pytest configuration.

Its presence at the repository root ensures the repo root is added to
``sys.path`` so tests can import the ``backend`` package (matching how the
FastAPI app is run, e.g. ``uvicorn backend.main:app``).
"""
