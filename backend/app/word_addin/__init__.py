"""Word add-in endpoints.

A thin API surface consumed by the Microsoft Word task-pane add-in (see the
top-level ``word-addin/`` project). It reuses the existing Claude client to
turn raw contract text pulled out of Word into structured, redline-ready
findings — it deliberately owns no models or storage of its own.
"""
