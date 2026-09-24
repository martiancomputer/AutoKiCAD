"""Loader for the generated protobuf modules.

protoc emits imports that are relative to `--proto_path`, so
`board/board_commands_pb2.py` contains `from common.types import base_types_pb2`.
Those only resolve if the generation root is itself on `sys.path`. Rather than
rewrite generated code, we put `_proto/` on the path once, here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

PROTO_DIR = Path(__file__).resolve().parent / "_proto"


class ProtosUnavailable(RuntimeError):
    """Generated bindings are missing, or the protobuf runtime is absent."""


def _ensure_path() -> None:
    p = str(PROTO_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def available() -> bool:
    if not PROTO_DIR.is_dir() or not any(PROTO_DIR.rglob("*_pb2.py")):
        return False
    try:
        import google.protobuf  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def load(dotted: str) -> ModuleType:
    """Import a generated module, e.g. `common.envelope_pb2`.

    Raises `ProtosUnavailable` with actionable instructions rather than a bare
    ImportError, because there are two distinct causes and different fixes.
    """
    if not PROTO_DIR.is_dir() or not any(PROTO_DIR.rglob("*_pb2.py")):
        raise ProtosUnavailable(
            "protobuf bindings have not been generated.\n"
            "    python -m autokicad.ipc.codegen"
        )
    try:
        import google.protobuf  # noqa: F401,PLC0415
    except ImportError as exc:
        raise ProtosUnavailable(
            "the Python protobuf runtime is not installed.\n"
            "    sudo pacman -S python-protobuf\n"
            "It must match the protoc used for codegen (both 35.x here)."
        ) from exc

    _ensure_path()
    import importlib

    try:
        return importlib.import_module(dotted)
    except ImportError as exc:
        raise ProtosUnavailable(
            f"could not import generated module {dotted!r}: {exc}\n"
            "Regenerate with: python -m autokicad.ipc.codegen"
        ) from exc


# Convenience accessors for the modules the client actually needs.
def envelope() -> ModuleType:
    return load("common.envelope_pb2")


def base_commands() -> ModuleType:
    return load("common.commands.base_commands_pb2")


def editor_commands() -> ModuleType:
    return load("common.commands.editor_commands_pb2")


def project_commands() -> ModuleType:
    return load("common.commands.project_commands_pb2")


def base_types() -> ModuleType:
    return load("common.types.base_types_pb2")


def enums() -> ModuleType:
    """common/types/enums.proto -- note Units lives here, not in base_types."""
    return load("common.types.enums_pb2")


def board_commands() -> ModuleType:
    return load("board.board_commands_pb2")


def board_types() -> ModuleType:
    return load("board.board_types_pb2")


def schematic_types() -> ModuleType:
    return load("schematic.schematic_types_pb2")


def schematic_commands() -> ModuleType:
    return load("schematic.schematic_commands_pb2")
