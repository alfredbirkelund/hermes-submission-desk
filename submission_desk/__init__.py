"""Submission Desk: local application-readiness ledger for Hermes Agent."""

PLUGIN_ID = "submission-desk"
__version__ = "0.1.0"
# Bumped whenever a machine check can produce a different result for the same bytes and
# rules. Stored with every check result, so results from an older engine are recomputed.
# 2: unknowns caused by the environment (missing PDF engine) are no longer cached per revision.
ENGINE_VERSION = "sd-checks/2"
