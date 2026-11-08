"""Submission Desk: Hermes plugin entry point.

Agent tools propose and check; the applicant decides through the Desktop page or
``/submission-desk``. Data lives in the active profile's plugin-data directory.
"""


def register(ctx):
    from .submission_desk import commands, tools

    tools.register(ctx)
    commands.register(ctx)
