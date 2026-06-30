import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


GNUPLOT_START_RE = re.compile(r"^[ \t]*~~~gnuplot\b")
PYTHONPLOT_START_RE = re.compile(r"^[ \t]*~~~pythonplot\b")
FENCE_RE = re.compile(r"^[ \t]*~~~[ \t]*$")
SET_OUTPUT_RE = re.compile(r"""^[ \t]*set[ \t]+output[ \t]+(['"])(?P<path>[^'"]+)\1""")
SET_TERM_RE = re.compile(r"^[ \t]*set[ \t]+term")
RESET_RE = re.compile(r"^[ \t]*reset\b")
SAVEFIG_RE = re.compile(r"""savefig[ \t]*[(][ \t]*(['"])(?P<path>[^'"]+)\1""")


@dataclass(frozen=True)
class PlotBlock:
    kind: str
    index: int
    output: str | None


@dataclass(frozen=True)
class PlotGenerationResult:
    sources: int = 0
    scripts: int = 0
    commands: int = 0
    failures: tuple[str, ...] = ()


def expected_plot_artifacts(source: str) -> tuple[str, ...]:
    artifacts = []
    for block in extract_plot_blocks(source):
        if block.output is not None:
            artifacts.append(block.output)
        elif block.kind == "gnuplot":
            artifacts.append(f"_plot{block.index}.svg")
    return tuple(unique_visible_artifacts(artifacts))


def expected_plot_artifacts_for_source(source_path: Path) -> tuple[str, ...]:
    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ()
    return expected_plot_artifacts(source)


def extract_plot_blocks(source: str) -> tuple[PlotBlock, ...]:
    blocks = []
    kind = None
    output = None
    indexes = {"gnuplot": 0, "pythonplot": 0}
    for line in source.splitlines():
        if kind is not None:
            if FENCE_RE.match(line):
                blocks.append(PlotBlock(kind=kind, index=indexes[kind], output=normalize_plot_output(output)))
                indexes[kind] += 1
                kind = None
                output = None
                continue
            match = SET_OUTPUT_RE.search(line) if kind == "gnuplot" else SAVEFIG_RE.search(line)
            if match:
                output = match.group("path")
            continue

        if GNUPLOT_START_RE.match(line):
            kind = "gnuplot"
        elif PYTHONPLOT_START_RE.match(line):
            kind = "pythonplot"
    return tuple(blocks)


def gnuplot_script_from_source(source: str, pdf: bool = False) -> str:
    ext = ".pdf" if pdf else ".svg"
    term_variable = "@PDF" if pdf else "@SVG"
    lines = []
    in_plot = False
    output = ""
    term = ""
    nplots = 0

    for line in source.splitlines():
        if in_plot and FENCE_RE.match(line):
            in_plot = False
            lines.append("set output")
            if output.endswith(".png"):
                lines.append(f"! mogrify -trim {output}")
            elif output.endswith(ext):
                lines.append(fixfonts_command(output))
            elif not output:
                lines.append(fixfonts_command(f"_plot{nplots}{ext}"))
            nplots += 1
            continue

        if in_plot:
            if RESET_RE.match(line):
                lines.append(f"reset; set term {term_variable}; {defaults()}")
                term = ""
                continue
            output_match = SET_OUTPUT_RE.match(line)
            if output_match:
                output = output_match.group("path")
                if not term and output.endswith(".png"):
                    lines.append('set term @PNG enhanced font ",10";')
            elif SET_TERM_RE.match(line):
                term = line
            lines.append(line)
            continue

        if GNUPLOT_START_RE.match(line):
            in_plot = True
            output = ""
            term = ""
            lines.append(f"set output '_plot{nplots}{ext}'; {defaults()}")
        elif not line.lstrip().startswith("~~~"):
            lines.append(f"# {line}")

    return "\n".join(lines) + ("\n" if lines and nplots else "")


def python_script_from_source(source: str) -> str:
    lines = ["# -*- coding: utf-8 -*-"]
    in_plot = False
    indent = ""
    found = False

    for line in source.splitlines():
        if in_plot and FENCE_RE.match(line):
            in_plot = False
            indent = ""
            continue

        if in_plot:
            if not indent and line.strip():
                indent = line[: len(line) - len(line.lstrip())]
            if indent and line.startswith(indent):
                line = line[len(indent) :]
            lines.append(line)
            continue

        lines.append(f"# {line}")
        if PYTHONPLOT_START_RE.match(line):
            in_plot = True
            found = True

    if not found:
        return ""
    return "\n".join(lines) + ("\n" if found else "")


def generate_plot_scripts_for_source(source_path: Path) -> tuple[str, str]:
    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "", ""
    return gnuplot_script_from_source(source), python_script_from_source(source)


def generate_plot_artifacts(
    jobs,
    gnuplot_command: str = "gnuplot",
    python_command: str | None = None,
    png_terminal: str = "pngcairo",
) -> PlotGenerationResult:
    failures = []
    sources = 0
    scripts = 0
    commands = 0
    python_command = python_command or sys.executable

    for job in jobs:
        gnuplot_script, python_script = generate_plot_scripts_for_source(job.source_path)
        if not gnuplot_script and not python_script:
            continue

        sources += 1
        job.artifact_dir.mkdir(parents=True, exist_ok=True)
        link_source_plot_inputs(job.source_path, job.artifact_dir)

        if gnuplot_script:
            scripts += 1
            (job.artifact_dir / "plots").write_text(gnuplot_script, encoding="utf-8")
            commands += 1
            result = run_gnuplot(job.artifact_dir, gnuplot_command, png_terminal)
            if result is not None:
                failures.append(f"{job.source_rel}: gnuplot failed: {result}")

        if python_script:
            scripts += 1
            (job.artifact_dir / "plots.py").write_text(python_script, encoding="utf-8")
            commands += 1
            result = run_pythonplot(job.artifact_dir, python_command)
            if result is not None:
                failures.append(f"{job.source_rel}: pythonplot failed: {result}")

    return PlotGenerationResult(
        sources=sources,
        scripts=scripts,
        commands=commands,
        failures=tuple(failures),
    )


def link_source_plot_inputs(source_path: Path, artifact_dir: Path) -> int:
    linked = 0
    source_stem = source_path.stem
    source_dir = source_path.parent
    aux_dir = source_dir / source_stem

    for path in sorted(source_dir.glob(f"{source_stem}.*")):
        if path == source_path or not is_linkable_plot_input(path):
            continue
        linked += symlink_plot_input(path, artifact_dir / path.name)

    if aux_dir.is_dir():
        for path in sorted(aux_dir.iterdir()):
            if is_linkable_plot_input(path):
                linked += symlink_plot_input(path, artifact_dir / path.name)

    return linked


def is_linkable_plot_input(path: Path) -> bool:
    return not path.name.startswith(".") and (path.is_file() or path.is_dir())


def symlink_plot_input(target: Path, link: Path) -> int:
    if link.exists() or link.is_symlink():
        return 0
    relative_target = os.path.relpath(target, link.parent)
    link.symlink_to(relative_target, target_is_directory=target.is_dir())
    return 1


def run_gnuplot(artifact_dir: Path, command: str, png_terminal: str) -> str | None:
    svg = "svg enhanced font ',11'"
    pdf = "pdf mono enhanced font ',12' size 6,4"
    term = svg
    expression = f'batch=1; PNG="{png_terminal}"; SVG="{svg}"; PDF="{pdf}"; set macros; set term {term};'
    try:
        completed = subprocess.run(
            [command, "-e", expression, "plots"],
            cwd=artifact_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return str(exc)
    cleanup_gnuplot_outputs(artifact_dir)
    if completed.returncode:
        return (completed.stderr or completed.stdout or f"exit status {completed.returncode}").strip()
    return None


def run_pythonplot(artifact_dir: Path, command: str) -> str | None:
    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    try:
        completed = subprocess.run(
            [command, "plots.py"],
            cwd=artifact_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return str(exc)
    if completed.returncode:
        return (completed.stderr or completed.stdout or f"exit status {completed.returncode}").strip()
    return None


def defaults() -> str:
    return "set pointsize 0.75;"


def fixfonts_command(output: str) -> str:
    return f"! sed -i 's/font-size=\"\\([0-9.]*\\)\"/font-size=\"\\1pt\"/g' {output}"


def cleanup_gnuplot_outputs(artifact_dir: Path):
    (artifact_dir / "gnuplot.log").unlink(missing_ok=True)
    for path in artifact_dir.glob("_plot*.*"):
        try:
            if path.is_file() and path.stat().st_size == 0:
                path.unlink()
        except OSError:
            pass


def normalize_plot_output(output: str | None) -> str | None:
    if output is None:
        return None
    output = output.strip()
    if not output:
        return None
    path = PurePosixPath(output)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def unique_visible_artifacts(paths: list[str]) -> list[str]:
    visible = []
    seen = set()
    for path in paths:
        normalized = normalize_plot_output(path)
        if normalized is None or normalized in seen:
            continue
        if any(part.startswith(".") for part in PurePosixPath(normalized).parts):
            continue
        seen.add(normalized)
        visible.append(normalized)
    return visible
