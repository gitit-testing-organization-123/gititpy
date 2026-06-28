import argparse
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import SiteConfig
from wiki.static_site import StaticSiteBuilder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gititpy")
    parser.add_argument("--base-dir", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--wiki-title", default="GititPy", help="Site title.")
    parser.add_argument("--wiki-root", default=None, help="Wiki page tree. Defaults to BASE_DIR/wiki-pages.")
    parser.add_argument(
        "--mathjax-url",
        default="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js",
        help="MathJax script URL.",
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

    config = SiteConfig(
        base_dir=Path(args.base_dir).resolve(),
        wiki_title=args.wiki_title,
        wiki_root=Path(args.wiki_root).resolve() if args.wiki_root else None,
        source_root=Path(args.source_root).resolve() if args.source_root else None,
        build_source=not args.no_source,
        jobs=args.jobs,
        mathjax_url=args.mathjax_url,
    )

    if args.command == "build":
        build(config, args)
        return 0
    if args.command == "serve":
        output_dir = build(config, args)
        serve(output_dir, args.host, args.port)
        return 0
    parser.error(f"Unknown command {args.command}")
    return 2


def add_build_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--output", default="public", help="Directory for generated files.")
    parser.add_argument("--base-url", default="", help="Optional URL prefix, e.g. /repository-name.")
    parser.add_argument("--no-clean", action="store_true", help="Do not remove the output directory first.")
    parser.add_argument("--source-root", default=None, help="Source tree to render under /src/. Defaults to BASE_DIR/basilisk/src when it exists.")
    parser.add_argument("--no-source", action="store_true", help="Skip /src/ source browser generation.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"Parallel source render jobs. Defaults to {default_jobs()}.",
    )


def default_jobs() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def build(config: SiteConfig, args: argparse.Namespace) -> Path:
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = config.base_dir / output_dir
    builder = StaticSiteBuilder(
        config=config,
        output_dir=output_dir,
        base_url=args.base_url,
    )
    result = builder.build(clean=not args.no_clean)
    print(
        f"Built static site in {result.output_dir} "
        f"({result.html_files} HTML files, {result.copied_files} copied files)."
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
