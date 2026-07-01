import argparse
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import SiteConfig, load_config, replace_config
from wiki.site import StaticSiteBuilder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gititpy")
    parser.add_argument("--base-dir", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--config", default=None, help="Config file. Defaults to BASE_DIR/gititpy.toml when present.")
    parser.add_argument("--wiki-title", default=None, help="Site title.")
    parser.add_argument("--wiki-root", default=None, help="Wiki page tree. Defaults to BASE_DIR/wiki-pages.")
    parser.add_argument("--sandbox-root", default=None, help="Sandbox source tree. Defaults to BASE_DIR/sandbox when present.")
    parser.add_argument(
        "--toc",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable generated tables of contents.",
    )
    parser.add_argument(
        "--mathjax-url",
        default=None,
        help="MathJax script URL.",
    )
    parser.add_argument(
        "--template-root",
        action="append",
        default=None,
        help="Template override directory. Can be passed more than once.",
    )
    parser.add_argument(
        "--static-root",
        action="append",
        default=None,
        help="Static asset directory copied over packaged assets. Can be passed more than once.",
    )

    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Build the static site.")
    add_build_arguments(build_parser)

    serve_parser = subparsers.add_parser("serve", help="Build and serve the static site locally.")
    add_build_arguments(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host for the preview server.")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port for the preview server.")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    base_dir = Path(args.base_dir).resolve()
    config_path = Path(args.config).resolve() if args.config else None
    config = apply_cli_overrides(load_config(base_dir, config_path), args)

    if args.command == "build":
        build(config, force_rebuild=args.force_rebuild)
        return 0
    if args.command == "serve":
        output_dir = build(config, force_rebuild=args.force_rebuild)
        serve(output_dir, args.host, args.port)
        return 0
    parser.error(f"Unknown command {args.command}")
    return 2


def add_build_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--output", default=None, help="Directory for generated files.")
    parser.add_argument("--base-url", default=None, help="Optional URL prefix, e.g. /repository-name.")
    parser.add_argument("--artifacts-base-url", default=None, help="External base URL for /artifacts/... links.")
    parser.add_argument("--artifact-root", default=None, help="Local source artifact tree used when rewriting artifact links.")
    parser.add_argument("--sandbox-artifact-root", default=None, help="Local sandbox artifact tree used when rewriting artifact links.")
    parser.add_argument("--no-clean", action="store_true", help="Compatibility option; incremental builds no longer clean by default.")
    parser.add_argument("--force-rebuild", action="store_true", help="Ignore the incremental manifest and rebuild all generated files.")
    parser.add_argument("--source-root", default=None, help="Source tree to render under /src/. Defaults to BASE_DIR/basilisk/src when it exists.")
    parser.add_argument("--no-source", action="store_true", help="Skip /src/ source browser generation.")
    parser.add_argument(
        "--source-tags",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Generate qcc .tags files for C source pages.",
    )
    parser.add_argument("--qcc-command", default=None, help="qcc command used for --source-tags.")
    parser.add_argument(
        "--toc",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable generated tables of contents.",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Print build phases and generated paths.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"Parallel source render jobs. Defaults to {default_jobs()}.",
    )


def default_jobs() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def apply_cli_overrides(config: SiteConfig, args: argparse.Namespace) -> SiteConfig:
    changes = {}
    if args.wiki_title is not None:
        changes["wiki_title"] = args.wiki_title
    if args.wiki_root is not None:
        changes["wiki_root"] = Path(args.wiki_root)
    if args.sandbox_root is not None:
        changes["sandbox_root"] = Path(args.sandbox_root)
    if args.source_root is not None:
        changes["source_root"] = Path(args.source_root)
        changes["build_source"] = True
    if args.output is not None:
        changes["output_dir"] = Path(args.output)
    if args.base_url is not None:
        changes["base_url"] = args.base_url
    if args.artifacts_base_url is not None:
        changes["artifact_base_url"] = args.artifacts_base_url
    if args.artifact_root is not None:
        changes["artifact_root"] = Path(args.artifact_root)
    if args.sandbox_artifact_root is not None:
        changes["sandbox_artifact_root"] = Path(args.sandbox_artifact_root)
    if args.jobs is not None:
        changes["jobs"] = args.jobs
    if args.verbose is not None:
        changes["verbose"] = args.verbose
    if args.toc is not None:
        changes["table_of_contents"] = args.toc
    if args.source_tags is not None:
        changes["generate_source_tags"] = args.source_tags
    if args.qcc_command is not None:
        changes["qcc_command"] = args.qcc_command
    if args.mathjax_url is not None:
        changes["mathjax_url"] = args.mathjax_url
    if args.template_root is not None:
        changes["template_roots"] = tuple(Path(path) for path in args.template_root)
    if args.static_root is not None:
        changes["static_roots"] = tuple(Path(path) for path in args.static_root)
    if args.no_source:
        changes["build_source"] = False
    return replace_config(config, **changes)


def build(config: SiteConfig, force_rebuild: bool = False) -> Path:
    output_dir = config.resolved_output_dir()
    builder = StaticSiteBuilder(
        config=config,
        output_dir=output_dir,
        base_url=config.base_url,
    )
    result = builder.build(clean=force_rebuild, force_rebuild=force_rebuild)
    print(
        f"Built static site in {result.output_dir} "
        f"({result.html_files} HTML files, {result.copied_files} copied files, "
        f"{result.skipped_files} skipped)."
    )
    for warning in result.warnings:
        print(f"warning: {warning}")
    return result.output_dir


def serve(output_dir: Path, host: str, port: int):
    handler = partial(SimpleHTTPRequestHandler, directory=str(output_dir))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Serving {output_dir} at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
