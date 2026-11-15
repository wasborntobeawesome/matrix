"""Where the Studio keeps its runtime data.

Default: everything under video/ (projects/, library/, inbox/, templates/, comfy-workflows/), as before.
Environment overrides (used by the test suite and by a second test instance, so they never touch the data,
job state or warm workers of the Studio the user is running):

    STUDIO_DATA_DIR       projects/, library/ (music, b-roll, portraits, job state, monitor history), inbox/
    STUDIO_TEMPLATES_DIR  installed templates (default video/templates)
    STUDIO_WORKFLOWS_DIR  ComfyUI workflow library (default video/comfy-workflows)
"""
from __future__ import annotations

import os
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
DATA = Path(os.environ.get("STUDIO_DATA_DIR") or VIDEO).resolve()
PROJECTS = DATA / "projects"
LIB = DATA / "library"
INBOX = DATA / "inbox"
TEMPLATES = Path(os.environ.get("STUDIO_TEMPLATES_DIR") or VIDEO / "templates").resolve()
WORKFLOWS = Path(os.environ.get("STUDIO_WORKFLOWS_DIR") or VIDEO / "comfy-workflows").resolve()
