"""Dark Fleet Python layer: Hunter trackers, scripted policies, episode runner and evaluation.

Import discipline (enforced by python/tests/test_import_boundary.py):
  * tracker.py and policies.py consume ONLY role-filtered observation documents.
  * Only episode.py and evaluate.py (the harness) may import ``darkfleet_core.evaluation``,
    which exposes truth for scoring.
"""
