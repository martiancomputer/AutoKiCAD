"""AutoKiCAD -- agent-facing automation layer over KiCad.

The observe step of the agent loop. Import the domain model and a backend:

    from autokicad import CliBackend
    obs = CliBackend().observe("board.kicad_pcb", layers=["F.Cu", "B.Cu"])
    print(obs.to_agent_text())
    if obs.is_clean:
        ...
"""

from .backend import Backend, CliBackend, KicadCliError, clean_env
from .models import (
    AffectedItem,
    Category,
    IgnoredCheck,
    Observation,
    Position,
    Severity,
    Violation,
)

__all__ = [
    "AffectedItem",
    "Backend",
    "Category",
    "CliBackend",
    "IgnoredCheck",
    "KicadCliError",
    "Observation",
    "Position",
    "Severity",
    "Violation",
    "clean_env",
]
