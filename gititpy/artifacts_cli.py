import argparse
from pathlib import Path

from .config import load_config, replace_config
from wiki.artifacts import (
    artifact_manifest,
    artifact_roots_from_config,
    discover_artifact_jobs,
    jobs_to_json,
    manifest_to_json,
    stage_artifact_tree,
    stage_artifacts,
)
from wiki.plots import generate_plot_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gititpy-artifacts")
    parser.add_argument("--base-dir", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--config", default=None, help="Config file. Defaults to BASE_DIR/gititpy.toml when present.")
    parser.add_argument("--source-root", default=None, help="Source tree to scan. Defaults to BASE_DIR/basilisk/src when it exists.")
    parser.add_argument("--sandbox-root", default=None, help="Sandbox tree to scan. Defaults to BASE_DIR/sandbox when it exists.")
    parser.add_argument("--artifact-root", default=None, help="Tree containing generated artifacts for source files.")
    parser.add_argument("--sandbox-artifact-root", default=None, help="Tree containing generated artifacts for sandbox files.")
    parser.add_argument(
        "--scope",
        choices=("all", "source", "sandbox"),
        default="all",
        help="Artifact tree to operate on. Defaults to all.",
    )

    subparsers = parser.add_subparsers(dest="command")
    list_parser = subparsers.add_parser("list", help="List detected artifact-producing C files.")
    list_parser.add_argument("--all", action="store_true", help="Include C files with no detected artifacts.")
    list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    stage_parser = subparsers.add_parser("stage", help="Copy existing artifacts to a staging directory.")
    stage_parser.add_argument("--dest", required=True, help="Destination directory.")
    manifest_parser = subparsers.add_parser("manifest", help="Print a content-hash manifest for existing artifacts.")
    manifest_parser.add_argument("--output", default=None, help="Optional path to write the manifest JSON.")
    plots_parser = subparsers.add_parser("plots", help="Inspect or generate inline gnuplot/pythonplot artifacts.")
    plots_subparsers = plots_parser.add_subparsers(dest="plots_command")
    plots_list_parser = plots_subparsers.add_parser("list", help="List derived plot artifacts.")
    plots_list_parser.add_argument("--json", action="store_true", help="Print full artifact jobs as JSON.")
    plots_generate_parser = plots_subparsers.add_parser("generate", help="Generate inline plot artifacts.")
    plots_generate_parser.add_argument("--gnuplot-command", default="gnuplot", help="gnuplot command.")
    plots_generate_parser.add_argument(
        "--python-command",
        default=None,
        help="Python command for pythonplot blocks. Defaults to the current interpreter.",
    )
    plots_generate_parser.add_argument("--png-terminal", default="pngcairo", help="gnuplot terminal macro used for PNG output.")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    base_dir = Path(args.base_dir).resolve()
    config_path = Path(args.config).resolve() if args.config else None
    config = load_config(base_dir, config_path)
    changes = {}
    if args.source_root is not None:
        changes["source_root"] = Path(args.source_root)
        changes["build_source"] = True
    if args.sandbox_root is not None:
        changes["sandbox_root"] = Path(args.sandbox_root)
    if args.artifact_root is not None:
        changes["artifact_root"] = Path(args.artifact_root)
    if args.sandbox_artifact_root is not None:
        changes["sandbox_artifact_root"] = Path(args.sandbox_artifact_root)
    if changes:
        config = replace_config(config, **changes)

    if args.command == "list":
        return list_artifacts(config, scope=args.scope, include_empty=args.all, as_json=args.json)
    if args.command == "stage":
        return stage(config, Path(args.dest), scope=args.scope)
    if args.command == "manifest":
        return manifest(config, scope=args.scope, output=Path(args.output) if args.output else None)
    if args.command == "plots":
        if args.plots_command == "list":
            return list_plot_artifacts(config, scope=args.scope, as_json=args.json)
        if args.plots_command == "generate":
            return generate_plots(
                config,
                scope=args.scope,
                gnuplot_command=args.gnuplot_command,
                python_command=args.python_command,
                png_terminal=args.png_terminal,
            )
        plots_parser.print_help()
        return 2

    parser.error(f"Unknown command {args.command}")
    return 2


def list_artifacts(config, scope: str = "all", include_empty: bool = False, as_json: bool = False) -> int:
    jobs = discover_artifact_jobs(scoped_artifact_roots(config, scope), include_empty=include_empty)
    if as_json:
        print(jobs_to_json(jobs))
        return 0

    for job in jobs:
        print(f"{job.root_name}:{job.source_rel}")
        if job.existing_artifacts:
            print(f"  existing: {job.artifact_dir}")
            for artifact in job.existing_artifacts:
                print(f"    {job.artifact_key_dir}/{artifact}")
        if job.referenced_artifacts:
            print("  referenced:")
            for artifact in job.referenced_artifacts:
                print(f"    {job.artifact_key_dir}/{artifact}")
        if job.derived_artifacts:
            print("  derived:")
            for artifact in job.derived_artifacts:
                print(f"    {job.artifact_key_dir}/{artifact}")
        if not job.has_artifacts:
            print(f"  no artifacts detected; expected directory {job.artifact_dir}")
    return 0


def stage(config, destination: Path, scope: str = "all") -> int:
    copied = 0
    unstaged_roots = []

    if scope in {"all", "source"}:
        artifact_root = config.resolved_artifact_root()
        if artifact_root is not None:
            copied += stage_artifact_tree(artifact_root, destination, publish_prefix="src")
        else:
            unstaged_roots.extend(artifact_roots_from_config(config, include_source=True, include_sandbox=False))

    if scope in {"all", "sandbox"}:
        sandbox_artifact_root = config.resolved_sandbox_artifact_root()
        if sandbox_artifact_root is not None:
            copied += stage_artifact_tree(sandbox_artifact_root, destination, publish_prefix="sandbox")
        else:
            unstaged_roots.extend(artifact_roots_from_config(config, include_source=False, include_sandbox=True))

    if unstaged_roots:
        copied += stage_artifacts(discover_artifact_jobs(unstaged_roots), destination)
    print(f"Copied {copied} artifact file(s) to {destination}.")
    return 0


def manifest(config, scope: str = "all", output: Path | None = None) -> int:
    jobs = discover_artifact_jobs(scoped_artifact_roots(config, scope))
    data = manifest_to_json(artifact_manifest(jobs))
    if output is None:
        print(data)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(data + "\n", encoding="utf-8")
        print(f"Wrote {output}.")
    return 0


def list_plot_artifacts(config, scope: str = "all", as_json: bool = False) -> int:
    jobs = [job for job in discover_artifact_jobs(scoped_artifact_roots(config, scope)) if job.derived_artifacts]
    if as_json:
        print(jobs_to_json(jobs))
        return 0

    for job in jobs:
        print(f"{job.root_name}:{job.source_rel}")
        print(f"  artifact directory: {job.artifact_dir}")
        for artifact in job.derived_artifacts:
            print(f"    {job.artifact_key_dir}/{artifact}")
    return 0


def generate_plots(config, scope: str, gnuplot_command: str, python_command: str | None, png_terminal: str) -> int:
    jobs = [job for job in discover_artifact_jobs(scoped_artifact_roots(config, scope)) if job.derived_artifacts]
    result = generate_plot_artifacts(
        jobs,
        gnuplot_command=gnuplot_command,
        python_command=python_command,
        png_terminal=png_terminal,
    )
    print(
        f"Generated plot artifacts for {result.sources} source file(s): "
        f"{result.scripts} script file(s), {result.commands} command(s)."
    )
    for failure in result.failures:
        print(f"warning: {failure}")
    return 1 if result.failures else 0


def scoped_artifact_roots(config, scope: str):
    return artifact_roots_from_config(
        config,
        include_source=scope in {"all", "source"},
        include_sandbox=scope in {"all", "sandbox"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
