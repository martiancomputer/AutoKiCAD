"""Backends that produce an `Observation` from a board.

Today: `CliBackend`, shelling out to the installed `kicad-cli`. This works
against released KiCad (tested on 10.0.5) and needs no dev build.

Later: an `IpcBackend` speaking protobuf/nng to `kicad-cli api-server`, which
only exists on KiCad master. Both satisfy `Backend`, so agent-facing code that
depends on the protocol rather than the class does not change when we switch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import (
    Category,
    IgnoredCheck,
    Observation,
    Violation,
)

# Environment variables that AppImage runtimes export and that KiCad honours for
# its own path resolution. If the parent process is an AppImage (Claude Desktop
# sets APPDIR=/tmp/.mount_claudeXXXX), an unmodified environment makes kicad-cli
# look for `_pcbnew.kiface` inside *that* mount and fail with:
#     Failed to load shared library '.../usr/bin/_pcbnew.kiface'
# Stripping them restores normal /usr-relative resolution. This bit us for real,
# so do not remove it without testing from inside an AppImage-hosted shell.
_APPIMAGE_VARS = ("APPDIR", "APPIMAGE", "OWD", "ARGV0")


class KicadCliError(RuntimeError):
    """kicad-cli could not be run, or produced no usable output."""


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for var in _APPIMAGE_VARS:
        env.pop(var, None)
    return env


@runtime_checkable
class Backend(Protocol):
    """What the agent's observe step depends on. Keep this surface small."""

    def observe(self, board: Path | str, **kwargs) -> Observation: ...


@dataclass
class CliBackend:
    """Drives the `kicad-cli` binary.

    `timeout` is per-invocation. DRC on a large board with zone refill is not
    fast, so the default is generous.
    """

    exe: str = "kicad-cli"
    timeout: int = 900

    def __post_init__(self) -> None:
        resolved = shutil.which(self.exe, path=clean_env().get("PATH"))
        if resolved is None:
            raise KicadCliError(
                f"{self.exe!r} not found on PATH. Install KiCad, or pass "
                "exe=/path/to/kicad-cli."
            )
        self.exe = resolved

    # ---- plumbing --------------------------------------------------------

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.exe, *args],
                env=clean_env(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise KicadCliError(
                f"kicad-cli timed out after {self.timeout}s: {' '.join(args)}"
            ) from exc

    def version(self) -> str:
        return self._run(["version"]).stdout.strip()

    # ---- DRC -------------------------------------------------------------

    def drc(
        self,
        board: Path | str,
        *,
        units: str = "mm",
        schematic_parity: bool = False,
        refill_zones: bool = True,
        all_track_errors: bool = False,
    ) -> Observation:
        """Run DRC and parse the JSON report.

        `refill_zones` defaults to True because stale zone fills produce
        phantom unconnected items, which would corrupt the routing-progress
        metric. It does not modify the board on disk (we never pass
        --save-board).

        Note: we deliberately do NOT pass --exit-code-violations. With it, a
        nonzero exit means "violations found", which is indistinguishable from
        "the tool failed". We determine violations from the parsed report and
        keep exit codes meaning only success/failure.
        """
        board = Path(board)
        if not board.is_file():
            raise KicadCliError(f"board not found: {board}")

        with tempfile.TemporaryDirectory(prefix="autokicad-drc-") as tmp:
            out = Path(tmp) / "drc.json"
            args = [
                "pcb", "drc",
                "--format", "json",
                "--severity-all",
                "--units", units,
                "-o", str(out),
            ]
            if schematic_parity:
                args.append("--schematic-parity")
            if refill_zones:
                args.append("--refill-zones")
            if all_track_errors:
                args.append("--all-track-errors")
            args.append(str(board))

            proc = self._run(args)

            if not out.is_file():
                raise KicadCliError(
                    "DRC produced no report.\n"
                    f"  exit={proc.returncode}\n"
                    f"  stdout: {proc.stdout.strip()[:400]}\n"
                    f"  stderr: {proc.stderr.strip()[:400]}"
                )
            raw = json.loads(out.read_text(encoding="utf-8"))

        return self._parse_drc(raw, fallback_source=str(board))

    @staticmethod
    def _parse_drc(raw: dict, *, fallback_source: str) -> Observation:
        buckets = (
            ("violations", Category.VIOLATION),
            ("unconnected_items", Category.UNCONNECTED),
            ("schematic_parity", Category.PARITY),
        )
        violations: list[Violation] = []
        for key, category in buckets:
            for entry in raw.get(key) or []:
                violations.append(Violation.from_json(entry, category))

        return Observation(
            source=str(raw.get("source") or fallback_source),
            kicad_version=str(raw.get("kicad_version", "")),
            coordinate_units=str(raw.get("coordinate_units", "mm")),
            violations=violations,
            ignored_checks=[
                IgnoredCheck(str(c.get("key", "")), str(c.get("description", "")))
                for c in raw.get("ignored_checks") or []
            ],
            included_severities=[str(s) for s in raw.get("included_severities") or []],
        )

    # ---- visuals ---------------------------------------------------------

    def render(
        self,
        board: Path | str,
        output: Path | str,
        *,
        side: str = "top",
        width: int = 1200,
        height: int = 900,
        quality: str = "basic",
        rotate: str | None = None,
        perspective: bool = False,
        zoom: float | None = None,
    ) -> Path:
        """3D render to PNG/JPEG. Good for a general look, poor for routing detail."""
        board, output = Path(board), Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "pcb", "render",
            "--side", side,
            "--width", str(width),
            "--height", str(height),
            "--quality", quality,
            "-o", str(output),
        ]
        if rotate:
            args += ["--rotate", rotate]
        if perspective:
            args.append("--perspective")
        if zoom is not None:
            args += ["--zoom", str(zoom)]
        args.append(str(board))

        proc = self._run(args)
        if not output.is_file():
            raise KicadCliError(
                f"render produced no image (exit={proc.returncode}): "
                f"{proc.stderr.strip()[:400]}"
            )
        return output

    def plot_layers(
        self,
        board: Path | str,
        outdir: Path | str,
        layers: list[str] | None = None,
        *,
        one_file_per_layer: bool = True,
        black_and_white: bool = False,
    ) -> list[Path]:
        """2D SVG plot. This is the useful visual for routing inspection.

        One file per layer lets an agent look at exactly F.Cu without every
        other layer overlaid on top of it.
        """
        board, outdir = Path(board), Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        layers = layers or ["F.Cu", "B.Cu"]

        args = [
            "pcb", "export", "svg",
            "--layers", ",".join(layers),
            "--page-size-mode", "2",   # board area only
            "--exclude-drawing-sheet",
            "-o", str(outdir),
        ]
        args.append("--mode-multi" if one_file_per_layer else "--mode-single")
        if black_and_white:
            args.append("--black-and-white")
        args.append(str(board))

        before = set(outdir.glob("*.svg"))
        proc = self._run(args)
        produced = sorted(set(outdir.glob("*.svg")) - before)

        if not produced:
            raise KicadCliError(
                f"svg export produced no files (exit={proc.returncode}): "
                f"{proc.stderr.strip()[:400]}"
            )
        return produced

    # ---- the agent's observe step ----------------------------------------

    def observe(
        self,
        board: Path | str,
        *,
        render: bool = False,
        layers: list[str] | None = None,
        artifacts: Path | str | None = None,
        **drc_kwargs,
    ) -> Observation:
        """DRC plus optional visuals, as one `Observation`.

        Structured findings are always gathered; visuals are opt-in because
        they cost seconds and, for most decisions, the numbers are the signal.
        """
        obs = self.drc(board, **drc_kwargs)

        if render or layers:
            dest = Path(artifacts) if artifacts else Path(tempfile.mkdtemp(
                prefix="autokicad-view-"))
            dest.mkdir(parents=True, exist_ok=True)
            stem = Path(board).stem

            if render:
                try:
                    obs.render_paths.append(
                        str(self.render(board, dest / f"{stem}-top.png"))
                    )
                except KicadCliError as exc:
                    # A failed visual must not sink the structured observation.
                    obs.render_paths.append(f"<render failed: {exc}>")
            if layers:
                try:
                    obs.render_paths.extend(
                        str(p) for p in self.plot_layers(board, dest, layers)
                    )
                except KicadCliError as exc:
                    obs.render_paths.append(f"<svg failed: {exc}>")

        return obs
