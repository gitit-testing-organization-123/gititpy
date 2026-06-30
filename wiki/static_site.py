import hashlib
import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gititpy.config import SiteConfig, default_config
from .darcsit import fenced_code, language_for_slug, render as render_darcsit, render_markdown
from .source import SourcePathError, SourceTree
from .storage import PageNameError, WikiRepository
from .tags import generate_qcc_tags, is_qcc_tags_file


ATTRIBUTE_VALUE_RE = r""""([^"]*)"|'([^']*)'|([^\s"'=<>`]+)"""
HREF_RE = re.compile(r"\bhref=(?:" + ATTRIBUTE_VALUE_RE + r")")
URL_ATTR_RE = re.compile(r"\b(href|src|poster)=(?:" + ATTRIBUTE_VALUE_RE + r")")
TEMP_PLOT_RE = re.compile(r"^_plot[0-9]+\.[A-Za-z0-9]+$")


@dataclass(frozen=True)
class StaticBuildResult:
    output_dir: Path
    html_files: int
    copied_files: int
    skipped_files: int = 0
    warnings: tuple[str, ...] = ()


class StaticSiteBuilder:
    def __init__(
        self,
        wiki_root: Path | None = None,
        source_root: Path | None = None,
        output_dir: Path | None = None,
        base_url: str = "",
        config: SiteConfig | None = None,
    ):
        self.config = config or default_config()
        self.repo = WikiRepository(wiki_root or self.config.resolved_wiki_root())
        resolved_sandbox_root = self.config.resolved_sandbox_root()
        self.sandbox_repo = (
            WikiRepository(resolved_sandbox_root, seed_defaults=False)
            if resolved_sandbox_root is not None
            else None
        )
        resolved_source_root = source_root if source_root is not None else self.config.resolved_source_root()
        self.source_tree = SourceTree(resolved_source_root) if resolved_source_root else None
        self.output_dir = Path(output_dir or self.config.base_dir / "public")
        self.urls = StaticUrls(base_url)
        self.templates = TemplateRenderer(self.config.resolved_template_roots())
        self.html_files = 0
        self.copied_files = 0
        self.skipped_files = 0
        self.jobs = self.resolve_jobs(self.config.jobs)
        self.verbose = self.config.verbose
        self.manifest_path = self.output_dir / ".gititpy-build.json"
        self.force_rebuild = False
        self.old_manifest: dict = {"items": {}}
        self.new_manifest: dict = {"version": 1, "items": {}}
        self.build_signature = self.compute_build_signature()
        self._counter_lock = threading.Lock()
        self._warnings: list[str] = []
        self._warning_keys: set[str] = set()

    def build(self, clean: bool = False, force_rebuild: bool = False) -> StaticBuildResult:
        self.force_rebuild = force_rebuild
        self.log(f"Preparing wiki root {self.repo.root}")
        self.repo.ensure_ready()
        if clean and self.output_dir.exists():
            self.log(f"Cleaning {self.output_dir}")
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.old_manifest = self.load_manifest()
        self.new_manifest = {"version": 1, "items": {}}

        self.log(f"Writing output to {self.output_dir}")
        self.log("Copying static assets")
        self.copy_static_assets()
        self.log("Rendering wiki pages")
        self.build_wiki()
        if self.sandbox_repo is not None:
            self.log(f"Rendering sandbox pages from {self.sandbox_repo.root}")
            self.build_sandbox()
        if self.source_tree is not None:
            self.log(f"Rendering source tree {self.source_tree.root} with {self.jobs} job(s)")
            self.build_source_tree()
        else:
            self.log("Skipping source tree")
        self.log("Writing search index")
        self.write_search_index()
        self.log("Writing sitemap and robots.txt")
        self.write_sitemap()
        self.write_robots_txt()
        self.remove_stale_manifest_outputs()
        self.write_manifest()

        return StaticBuildResult(
            output_dir=self.output_dir,
            html_files=self.html_files,
            copied_files=self.copied_files,
            skipped_files=self.skipped_files,
            warnings=tuple(self._warnings),
        )

    def build_wiki(self):
        self.render_all_pages_index()
        self.render_recent_page()
        self.render_search_page()

        for directory in self.visible_directories(self.repo.root):
            if self.is_shadowed_wiki_sandbox_path(directory):
                continue
            if directory != self.repo.root:
                self.render_wiki_directory(directory)

        for path in self.visible_files(self.repo.root):
            if self.is_shadowed_wiki_sandbox_path(path):
                continue
            rel_path = path.relative_to(self.repo.root)
            if self.is_renderable_wiki_file(path):
                self.render_wiki_page(self.repo.page_slug_for_path(rel_path))
            else:
                self.copy_file(path, self.output_dir / rel_path)

    def build_sandbox(self):
        if self.sandbox_repo is None:
            return
        self.sandbox_repo.ensure_ready()
        for directory in self.visible_directories(self.sandbox_repo.root):
            self.render_sandbox_directory(directory)

        for path in self.visible_files(self.sandbox_repo.root):
            rel_path = path.relative_to(self.sandbox_repo.root)
            if self.is_renderable_sandbox_file(path):
                self.render_sandbox_page(self.sandbox_repo.page_slug_for_path(rel_path))
            else:
                self.copy_file(path, self.output_dir / "sandbox" / rel_path)

    def render_wiki_page(self, slug: str):
        source = self.repo.read_page(slug)
        source_path = self.repo.page_path(slug)
        output_paths = [
            self.urls.page_output_path(self.output_dir, slug),
            self.urls.history_output_path(self.output_dir, slug),
        ]
        if slug == "FrontPage":
            output_paths.append(self.output_dir / "FrontPage.html")
        manifest_key = f"wiki:{slug}"
        if self.is_manifest_current(manifest_key, source_path, output_paths, "page"):
            self.record_manifest_item(manifest_key, source_path, output_paths, "page")
            self.copy_raw_page(slug, source_path)
            self.skip_file(f"Skip wiki page {slug}")
            return
        content_html = render_darcsit(
            source,
            slug,
            source_path=source_path,
            table_of_contents=self.config.table_of_contents,
        )
        context = self.page_context(slug) | {
            "content_html": self.rewrite_content_links(content_html),
            "revision": None,
            "source": source,
        }
        html = self.render_template("wiki/page.html", context)
        self.write_html(self.urls.page_output_path(self.output_dir, slug), html)

        if slug == "FrontPage":
            self.write_html(self.output_dir / "FrontPage.html", html)

        self.copy_raw_page(slug, source_path)
        self.render_history_page(slug)
        self.record_manifest_item(manifest_key, source_path, output_paths, "page")

    def render_sandbox_page(self, slug: str):
        if self.sandbox_repo is None:
            return
        source = self.sandbox_repo.read_page(slug)
        source_path = self.sandbox_repo.page_path(slug)
        output_paths = [
            self.urls.sandbox_page_output_path(self.output_dir, slug),
            self.urls.sandbox_history_output_path(self.output_dir, slug),
        ]
        manifest_key = f"sandbox:{slug}"
        if self.is_manifest_current(manifest_key, source_path, output_paths, "page"):
            self.record_manifest_item(manifest_key, source_path, output_paths, "page")
            self.copy_file(source_path, self.urls.sandbox_raw_output_path(self.output_dir, self.sandbox_repo, slug))
            self.skip_file(f"Skip sandbox page {slug}")
            return
        content_html = render_darcsit(
            source,
            slug,
            source_path=source_path,
            table_of_contents=self.config.table_of_contents,
        )
        context = self.sandbox_page_context(slug) | {
            "content_html": self.rewrite_content_links(
                content_html,
                current_artifact_rel_dir=artifact_key_dir_for_slug(slug, prefix="sandbox"),
            ),
            "revision": None,
            "source": source,
        }
        html = self.render_template("wiki/page.html", context)
        self.write_html(self.urls.sandbox_page_output_path(self.output_dir, slug), html)
        self.copy_file(source_path, self.urls.sandbox_raw_output_path(self.output_dir, self.sandbox_repo, slug))
        self.render_sandbox_history_page(slug)
        self.record_manifest_item(manifest_key, source_path, output_paths, "page")

    def render_wiki_directory(self, directory: Path):
        rel_slug = directory.relative_to(self.repo.root).as_posix()
        try:
            entries = self.repo.list_directory(rel_slug)
        except (NotADirectoryError, PageNameError):
            return
        entries = [self.wiki_entry_with_href(entry) for entry in entries]
        parent = directory_parent(rel_slug)
        context = self.base_context() | {
            "page_title": f"{rel_slug}/" if rel_slug else "Pages",
            "canonical_url": self.urls.directory_url(rel_slug),
            "directory_path": rel_slug,
            "parent_path": parent,
            "parent_url": self.urls.directory_url(parent) if parent else self.urls.index_url(),
            "entries": entries,
        }
        output = self.urls.directory_output_path(self.output_dir, rel_slug)
        self.write_html(output, self.render_template("wiki/directory_index.html", context))

    def render_sandbox_directory(self, directory: Path):
        if self.sandbox_repo is None:
            return
        rel_slug = directory.relative_to(self.sandbox_repo.root).as_posix()
        if rel_slug == ".":
            rel_slug = ""
        try:
            entries = self.sandbox_repo.list_directory(rel_slug or ".")
        except (NotADirectoryError, PageNameError):
            return
        entries = [self.sandbox_entry_with_href(entry) for entry in entries]
        parent = directory_parent(rel_slug)
        context = self.base_context() | {
            "page_title": f"sandbox/{rel_slug}/" if rel_slug else "sandbox/",
            "canonical_url": self.urls.sandbox_directory_url(rel_slug),
            "directory_path": f"sandbox/{rel_slug}".rstrip("/"),
            "parent_path": parent,
            "parent_url": self.urls.sandbox_directory_url(parent)
            if parent or rel_slug
            else self.urls.index_url(),
            "entries": entries,
        }
        output = self.urls.sandbox_directory_output_path(self.output_dir, rel_slug)
        self.write_html(output, self.render_template("wiki/directory_index.html", context))

    def render_all_pages_index(self):
        pages = [
            SimpleNamespace(label=slug, href=self.urls.page_url(slug))
            for slug in self.renderable_wiki_slugs()
        ]
        context = self.base_context() | {"pages": pages, "page_title": "All pages"}
        self.write_html(self.output_dir / "_index.html", self.render_template("wiki/index.html", context))

    def render_recent_page(self):
        context = self.base_context() | {
            "page_title": "Recent activity",
            "revisions": self.repo.recent(),
        }
        self.write_html(self.output_dir / "_recent.html", self.render_template("wiki/recent.html", context))

    def render_search_page(self):
        context = self.base_context() | {
            "page_title": "Search",
            "query": "",
            "results": [],
            "search_index_url": self.urls.search_index_url(),
        }
        self.write_html(self.output_dir / "_search.html", self.render_template("wiki/search.html", context))

    def render_history_page(self, slug: str):
        context = self.page_context(slug) | {"canonical_url": None, "revisions": self.repo.history(slug)}
        self.write_html(
            self.urls.history_output_path(self.output_dir, slug),
            self.render_template("wiki/history.html", context),
        )

    def render_sandbox_history_page(self, slug: str):
        if self.sandbox_repo is None:
            return
        context = self.sandbox_page_context(slug) | {
            "canonical_url": None,
            "revisions": self.sandbox_repo.history(slug),
        }
        self.write_html(
            self.urls.sandbox_history_output_path(self.output_dir, slug),
            self.render_template("wiki/history.html", context),
        )

    def build_source_tree(self):
        if self.source_tree is None or not self.source_tree.root.exists():
            return

        directories = list(self.visible_directories(self.source_tree.root))
        self.log(f"Rendering {len(directories)} source directories")
        for directory in directories:
            self.render_source_directory(directory)

        files = [path for path in self.visible_files(self.source_tree.root) if not is_qcc_tags_file(path)]
        self.log(f"Rendering/copying {len(files)} source file(s)")
        if self.jobs == 1:
            for path in files:
                self.render_source_file(path)
            return

        with ThreadPoolExecutor(max_workers=self.jobs) as executor:
            futures = [executor.submit(self.render_source_file, path) for path in files]
            for future in as_completed(futures):
                future.result()

    def render_source_file(self, path: Path):
        rel = path.relative_to(self.source_tree.root).as_posix()
        if self.will_render_source_path(path):
            output_path = self.urls.source_page_output_path(self.output_dir, rel)
            manifest_key = f"source-page:{rel}"
            if self.is_manifest_current(manifest_key, path, [output_path], "source-page"):
                self.record_manifest_item(manifest_key, path, [output_path], "source-page")
                self.skip_file(f"Skip source page /src/{rel}")
                return
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.copy_file(path, self.urls.source_asset_output_path(self.output_dir, rel))
                return
            self.generate_source_tags(path)
            content_html = self.render_source_content(source, rel, path)
            context = self.base_context() | {
                "page_title": f"/src/{rel}",
                "canonical_url": self.urls.source_page_url(rel),
                "source_path": rel,
                "content_html": self.rewrite_content_links(
                    content_html,
                    current_source_path=rel,
                    current_artifact_rel_dir=artifact_key_dir_for_slug(rel, prefix="src"),
                ),
            }
            self.write_html(
                output_path,
                self.render_template("wiki/source_page.html", context),
            )
            self.record_manifest_item(manifest_key, path, [output_path], "source-page")
        else:
            self.copy_file(path, self.urls.source_asset_output_path(self.output_dir, rel))

    def render_source_directory(self, directory: Path):
        rel = directory.relative_to(self.source_tree.root).as_posix()
        if rel == ".":
            rel = ""
        try:
            entries = self.source_tree.entries(rel)
        except (NotADirectoryError, SourcePathError):
            return
        entries = [self.source_entry_with_href(entry) for entry in entries]
        parent = source_parent(rel)
        context = self.base_context() | {
            "page_title": f"/src/{rel}" if rel else "/src",
            "canonical_url": self.urls.source_directory_url(rel),
            "source_path": rel,
            "parent_path": parent,
            "parent_url": self.urls.source_directory_url(parent)
            if parent
            else self.urls.source_root_url(),
            "entries": entries,
        }
        self.write_html(
            self.urls.source_directory_output_path(self.output_dir, rel),
            self.render_template("wiki/source_index.html", context),
        )

    def copy_static_assets(self):
        static_root = Path(__file__).resolve().parent / "static"
        if static_root.exists():
            self.log(f"Copy static {static_root} -> {self.output_dir / 'static'}")
            shutil.copytree(static_root, self.output_dir / "static", dirs_exist_ok=True)
        for static_root in self.config.resolved_static_roots():
            if static_root.exists():
                self.log(f"Copy static {static_root} -> {self.output_dir / 'static'}")
                shutil.copytree(static_root, self.output_dir / "static", dirs_exist_ok=True)

    def write_search_index(self):
        documents = []
        for slug in self.renderable_wiki_slugs():
            try:
                text = self.repo.read_page(slug)
            except FileNotFoundError:
                continue
            documents.append(
                {
                    "title": slug,
                    "url": self.urls.page_url(slug),
                    "text": text,
                }
            )
        if self.sandbox_repo is not None:
            for slug in self.renderable_sandbox_slugs():
                try:
                    text = self.sandbox_repo.read_page(slug)
                except FileNotFoundError:
                    continue
                documents.append(
                    {
                        "title": f"sandbox/{slug}",
                        "url": self.urls.sandbox_page_url(slug),
                        "text": text,
                    }
                )
        path = self.output_dir / "search-index.json"
        path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        self.log(f"Write {path}")
        self.copied_files += 1

    def write_sitemap(self):
        urls = self.sitemap_urls()
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for url in urls:
            lines.append("  <url>")
            lines.append(f"    <loc>{xml_escape(url)}</loc>")
            lines.append("  </url>")
        lines.append("</urlset>")
        path = self.output_dir / "sitemap.xml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log(f"Write {path}")
        self.copied_files += 1

    def sitemap_urls(self) -> list[str]:
        urls = [self.urls.front_url()]
        urls.extend(self.urls.page_url(slug) for slug in self.renderable_wiki_slugs() if slug != "FrontPage")
        urls.extend(self.urls.directory_url(slug) for slug in self.renderable_wiki_directory_slugs())

        if self.sandbox_repo is not None:
            urls.append(self.urls.sandbox_root_url())
            urls.extend(self.urls.sandbox_page_url(slug) for slug in self.renderable_sandbox_slugs())
            urls.extend(self.urls.sandbox_directory_url(slug) for slug in self.renderable_sandbox_directory_slugs())

        if self.source_tree is not None and self.source_tree.root.exists():
            urls.append(self.urls.source_root_url())
            urls.extend(self.urls.source_directory_url(path) for path in self.renderable_source_directory_paths())
            urls.extend(self.urls.source_page_url(path) for path in self.renderable_source_file_paths())

        return sorted(unique_urls(urls), key=str.casefold)

    def write_robots_txt(self):
        lines = [
            "User-agent: *",
            f"Allow: {self.urls.robots_path('/')}",
            f"Disallow: {self.urls.robots_path('/_raw/')}",
            f"Disallow: {self.urls.robots_path('/_history/')}",
            f"Disallow: {self.urls.robots_path('/_search.html')}",
            "",
            f"Sitemap: {self.urls.sitemap_url()}",
            "",
        ]
        path = self.output_dir / "robots.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.log(f"Write {path}")
        self.copied_files += 1

    def copy_raw_page(self, slug: str, source_path: Path):
        self.copy_file(source_path, self.urls.raw_output_path(self.output_dir, self.repo, slug))

    def copy_file(self, source: Path, destination: Path):
        manifest_key = f"copy:{self.relative_output_path(destination)}"
        if self.is_manifest_current(manifest_key, source, [destination], "copy"):
            self.record_manifest_item(manifest_key, source, [destination], "copy")
            self.skip_file(f"Skip copy {source} -> {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        self.log(f"Copy {source} -> {destination}")
        with self._counter_lock:
            self.copied_files += 1
        self.record_manifest_item(manifest_key, source, [destination], "copy")

    def write_html(self, path: Path, html: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        self.log(f"Write {path}")
        with self._counter_lock:
            self.html_files += 1

    def render_template(self, template: str, context: dict) -> str:
        return self.templates.render(template, context)

    def render_source_content(self, source: str, rel: str, path: Path) -> str:
        try:
            return render_darcsit(
                source,
                rel,
                source_path=path,
                table_of_contents=self.config.table_of_contents,
                basilisk_root=self.source_tree.root if self.source_tree is not None else None,
            )
        except RuntimeError as exc:
            language = language_for_slug(rel)
            if not language:
                raise
            self.warn(f"Rendered /src/{rel} as plain code after Darcsit failure: {exc}")
            return render_markdown(fenced_code(source, language), table_of_contents=False)

    def rewrite_content_links(
        self,
        html: str,
        current_source_path: str | None = None,
        current_artifact_rel_dir: str | None = None,
    ) -> str:
        html = self.rewrite_source_links(html, current_source_path=current_source_path)
        return self.rewrite_artifact_links(html, current_artifact_rel_dir=current_artifact_rel_dir)

    def rewrite_artifact_links(self, html: str, current_artifact_rel_dir: str | None = None) -> str:
        if not self.config.artifact_base_url:
            return html

        def replace(match: re.Match[str]) -> str:
            attribute = match.group(1)
            quote_char = '"' if match.group(2) is not None else "'" if match.group(3) is not None else '"'
            value = match.group(2) or match.group(3) or match.group(4)
            rewritten = self.rewrite_artifact_url(value, current_artifact_rel_dir=current_artifact_rel_dir)
            return f"{attribute}={quote_char}{rewritten}{quote_char}"

        return URL_ATTR_RE.sub(replace, html)

    def rewrite_artifact_url(self, value: str, current_artifact_rel_dir: str | None = None) -> str:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return value
        artifact_path = self.artifact_path_for_url(parsed.path, current_artifact_rel_dir)
        if artifact_path is None:
            return value
        artifact_base_url = self.config.artifact_base_url.rstrip("/")
        artifact_path = quote(artifact_path, safe="/._-~")
        rewritten = f"{artifact_base_url}/{artifact_path}"
        if parsed.query:
            rewritten = f"{rewritten}?{parsed.query}"
        if parsed.fragment:
            rewritten = f"{rewritten}#{parsed.fragment}"
        return rewritten

    def artifact_path_for_url(self, path: str, current_artifact_rel_dir: str | None = None) -> str | None:
        decoded = unquote(path)
        if decoded.startswith("/artifacts/"):
            rel = decoded.removeprefix("/artifacts/").strip("/")
            return normalized_relative_url_path(rel)

        absolute_source_artifact = self.absolute_source_artifact_path(decoded)
        if absolute_source_artifact is not None:
            return absolute_source_artifact

        current_artifact_rel_dir = normalized_relative_url_path(current_artifact_rel_dir or "")
        if current_artifact_rel_dir is None:
            return None

        temp_plot_artifact = self.temp_plot_artifact_path(decoded, current_artifact_rel_dir)
        if temp_plot_artifact is not None:
            return temp_plot_artifact

        if decoded.startswith("/"):
            return None

        artifact_stem = PurePosixPath(current_artifact_rel_dir).name
        rel_path = normalized_relative_url_path(decoded)
        if rel_path is None:
            return None
        rel_parts = PurePosixPath(rel_path).parts
        if not rel_parts or rel_parts[0] != artifact_stem or len(rel_parts) == 1:
            return None
        return (PurePosixPath(current_artifact_rel_dir) / PurePosixPath(*rel_parts[1:])).as_posix()

    def temp_plot_artifact_path(self, path: str, current_artifact_rel_dir: str) -> str | None:
        if not path.startswith("/"):
            return None
        name = PurePosixPath(path).name
        if not TEMP_PLOT_RE.match(name):
            return None
        return (PurePosixPath(current_artifact_rel_dir) / name).as_posix()

    def absolute_source_artifact_path(self, path: str) -> str | None:
        if self.source_tree is None or not path.startswith("/"):
            return None
        normalized_path = Path(os.path.normpath(path))
        try:
            source_rel = normalized_path.relative_to(self.source_tree.root.resolve())
        except ValueError:
            return None
        return prefixed_source_artifact_path(source_rel.as_posix())

    def generate_source_tags(self, path: Path):
        if self.source_tree is None or not self.config.generate_source_tags:
            return
        result = generate_qcc_tags(path, self.source_tree.root, self.config.qcc_command)
        if result.warning:
            if "command not found" in result.warning:
                self.warn_once(f"qcc-missing:{self.config.qcc_command}", result.warning)
            else:
                self.warn(result.warning)
        elif result.generated:
            self.log(f"Generate qcc tags {path}.tags")

    def rewrite_source_links(self, html: str, current_source_path: str | None = None) -> str:
        if self.source_tree is None:
            return html

        def replace(match: re.Match[str]) -> str:
            quote_char = '"' if match.group(1) is not None else "'" if match.group(2) is not None else '"'
            href = match.group(1) or match.group(2) or match.group(3)
            rewritten = self.rewrite_source_href(href, current_source_path=current_source_path)
            return f"href={quote_char}{rewritten}{quote_char}"

        return HREF_RE.sub(replace, html)

    def rewrite_source_href(self, href: str, current_source_path: str | None = None) -> str:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
            return href
        if parsed.path.endswith("/"):
            return href

        rel = self.source_rel_for_href_path(parsed.path, current_source_path=current_source_path)
        if rel is None or not self.is_renderable_source_rel(rel):
            return href

        rewritten = urlsplit(self.urls.source_page_url(rel))
        return urlunsplit(("", "", rewritten.path, parsed.query, parsed.fragment))

    def source_rel_for_href_path(self, path: str, current_source_path: str | None = None) -> str | None:
        decoded = unquote(path)
        if decoded.startswith("/src/"):
            return normalized_source_href_path(decoded.removeprefix("/src/"))
        elif not decoded.startswith("/") and current_source_path:
            candidates = [
                (PurePosixPath(current_source_path).parent / decoded).as_posix(),
                decoded,
            ]
            fallback = None
            for candidate in candidates:
                rel = normalized_source_href_path(candidate)
                if rel is None:
                    continue
                fallback = fallback or rel
                if self.is_renderable_source_rel(rel):
                    return rel
            return fallback
        else:
            return None

    def is_renderable_source_rel(self, rel: str) -> bool:
        if self.source_tree is None:
            return False
        try:
            path = self.source_tree.resolve(rel)
        except SourcePathError:
            return False
        return path.is_file() and self.will_render_source_path(path)

    def base_context(self) -> dict:
        return {
            "wiki_title": self.config.wiki_title,
            "canonical_url": None,
            "front_url": self.urls.front_url(),
            "all_pages_url": self.urls.index_url(),
            "recent_url": self.urls.recent_url(),
            "source_root_url": self.urls.source_root_url(),
            "search_url": self.urls.search_url(),
            "go_url": self.urls.search_url(),
            "help_url": self.urls.page_url("Help"),
            "mathjax_url": self.config.mathjax_url,
            "static_url": self.urls.static_url(),
            "static_build": True,
        }

    def page_context(self, slug: str) -> dict:
        return self.base_context() | {
            "page_slug": slug,
            "page_title": title_for(slug),
            "page_exists": self.repo.exists(slug),
            "page_url": self.urls.page_url(slug),
            "canonical_url": self.urls.page_url(slug),
            "edit_page_url": "",
            "history_page_url": self.urls.history_url(slug),
            "raw_page_url": self.urls.raw_url(self.repo, slug),
            "delete_page_url": "",
        }

    def sandbox_page_context(self, slug: str) -> dict:
        if self.sandbox_repo is None:
            raise PageNameError("Sandbox is not configured.")
        return self.base_context() | {
            "page_slug": f"sandbox/{slug}",
            "page_title": title_for(slug),
            "page_exists": self.sandbox_repo.exists(slug),
            "page_url": self.urls.sandbox_page_url(slug),
            "canonical_url": self.urls.sandbox_page_url(slug),
            "edit_page_url": "",
            "history_page_url": self.urls.sandbox_history_url(slug),
            "raw_page_url": self.urls.sandbox_raw_url(self.sandbox_repo, slug),
            "delete_page_url": "",
        }

    def wiki_entry_with_href(self, entry):
        if entry.is_dir:
            href = self.urls.directory_url(entry.slug.rstrip("/"))
        else:
            path = self.repo.page_path(entry.slug)
            href = self.urls.page_url(entry.slug) if self.is_renderable_wiki_file(path) else self.urls.wiki_asset_url(self.repo, entry.slug)
        return SimpleNamespace(name=entry.name, slug=entry.slug, is_dir=entry.is_dir, href=href)

    def sandbox_entry_with_href(self, entry):
        if self.sandbox_repo is None:
            href = self.urls.sandbox_directory_url(entry.slug.rstrip("/"))
            return SimpleNamespace(name=entry.name, slug=entry.slug, is_dir=entry.is_dir, href=href)
        if entry.is_dir:
            href = self.urls.sandbox_directory_url(entry.slug.rstrip("/"))
        else:
            path = self.sandbox_repo.page_path(entry.slug)
            href = (
                self.urls.sandbox_page_url(entry.slug)
                if self.is_renderable_sandbox_file(path)
                else self.urls.sandbox_asset_url(self.sandbox_repo, entry.slug)
            )
        return SimpleNamespace(name=entry.name, slug=entry.slug, is_dir=entry.is_dir, href=href)

    def source_entry_with_href(self, entry):
        if entry.is_dir:
            href = self.urls.source_directory_url(entry.path)
        else:
            if self.source_tree is None:
                href = self.urls.source_asset_url(entry.path)
                return SimpleNamespace(name=entry.name, path=entry.path, is_dir=entry.is_dir, href=href)
            path = self.source_tree.resolve(entry.path)
            href = (
                self.urls.source_page_url(entry.path)
                if self.will_render_source_path(path)
                else self.urls.source_asset_url(entry.path)
            )
        return SimpleNamespace(name=entry.name, path=entry.path, is_dir=entry.is_dir, href=href)

    def renderable_wiki_slugs(self) -> list[str]:
        slugs = []
        for path in self.visible_files(self.repo.root):
            if self.is_shadowed_wiki_sandbox_path(path):
                continue
            if self.is_renderable_wiki_file(path):
                slugs.append(self.repo.page_slug_for_path(path.relative_to(self.repo.root)))
        return sorted(slugs, key=str.casefold)

    def renderable_wiki_directory_slugs(self) -> list[str]:
        slugs = []
        for path in self.visible_directories(self.repo.root):
            if path == self.repo.root or self.is_shadowed_wiki_sandbox_path(path):
                continue
            slugs.append(path.relative_to(self.repo.root).as_posix())
        return sorted(slugs, key=str.casefold)

    def renderable_sandbox_slugs(self) -> list[str]:
        if self.sandbox_repo is None:
            return []
        slugs = []
        for path in self.visible_files(self.sandbox_repo.root):
            if self.is_renderable_sandbox_file(path):
                slugs.append(self.sandbox_repo.page_slug_for_path(path.relative_to(self.sandbox_repo.root)))
        return sorted(slugs, key=str.casefold)

    def renderable_sandbox_directory_slugs(self) -> list[str]:
        if self.sandbox_repo is None:
            return []
        slugs = []
        for path in self.visible_directories(self.sandbox_repo.root):
            if path == self.sandbox_repo.root:
                continue
            slugs.append(path.relative_to(self.sandbox_repo.root).as_posix())
        return sorted(slugs, key=str.casefold)

    def renderable_source_directory_paths(self) -> list[str]:
        if self.source_tree is None or not self.source_tree.root.exists():
            return []
        paths = []
        for path in self.visible_directories(self.source_tree.root):
            if path == self.source_tree.root:
                continue
            paths.append(path.relative_to(self.source_tree.root).as_posix())
        return sorted(paths, key=str.casefold)

    def renderable_source_file_paths(self) -> list[str]:
        if self.source_tree is None or not self.source_tree.root.exists():
            return []
        paths = []
        for path in self.visible_files(self.source_tree.root):
            if is_qcc_tags_file(path) or not self.will_render_source_path(path):
                continue
            paths.append(path.relative_to(self.source_tree.root).as_posix())
        return sorted(paths, key=str.casefold)

    def is_renderable_wiki_file(self, path: Path) -> bool:
        slug = self.repo.page_slug_for_path(path.relative_to(self.repo.root))
        suffix = PurePosixPath(slug).suffix.lower()
        return path.suffix.lower() in {".md", ".page"} or suffix == ".bib" or language_for_slug(slug) is not None

    def is_renderable_sandbox_file(self, path: Path) -> bool:
        if self.sandbox_repo is None:
            return False
        slug = self.sandbox_repo.page_slug_for_path(path.relative_to(self.sandbox_repo.root))
        suffix = PurePosixPath(slug).suffix.lower()
        return path.suffix.lower() in {".md", ".page"} or suffix == ".bib" or language_for_slug(slug) is not None

    def is_shadowed_wiki_sandbox_path(self, path: Path) -> bool:
        if self.sandbox_repo is None:
            return False
        rel = path.relative_to(self.repo.root)
        return bool(rel.parts) and rel.parts[0] == "sandbox"

    def will_render_source_path(self, path: Path) -> bool:
        if self.source_tree is None or not self.source_tree.should_render(path):
            return False
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False
        return True

    def visible_directories(self, root: Path):
        for path in [root, *root.rglob("*")]:
            if path.is_dir() and self.is_visible_path(root, path):
                yield path

    def visible_files(self, root: Path):
        for path in root.rglob("*"):
            if path.is_file() and self.is_visible_path(root, path):
                yield path

    def is_visible_path(self, root: Path, path: Path) -> bool:
        rel = path.relative_to(root)
        return ".git" not in rel.parts and not any(part.startswith(".") for part in rel.parts)

    def resolve_jobs(self, jobs: int | None) -> int:
        if jobs is None:
            return max(1, min(4, os.cpu_count() or 1))
        return max(1, jobs)

    def warn(self, message: str):
        with self._counter_lock:
            self._warnings.append(message)

    def warn_once(self, key: str, message: str):
        with self._counter_lock:
            if key in self._warning_keys:
                return
            self._warning_keys.add(key)
            self._warnings.append(message)

    def skip_file(self, message: str):
        self.log(message)
        with self._counter_lock:
            self.skipped_files += 1

    def load_manifest(self) -> dict:
        if self.force_rebuild or not self.manifest_path.exists():
            return {"items": {}}
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"items": {}}
        if manifest.get("version") != 1:
            return {"items": {}}
        return manifest

    def write_manifest(self):
        self.manifest_path.write_text(
            json.dumps(self.new_manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.log(f"Write {self.manifest_path}")

    def is_manifest_current(self, key: str, source: Path, outputs: list[Path], kind: str) -> bool:
        if self.force_rebuild:
            return False
        current = self.manifest_item(source, outputs, kind)
        previous = self.old_manifest.get("items", {}).get(key)
        if previous != current:
            return False
        return all(output.exists() for output in outputs)

    def record_manifest_item(self, key: str, source: Path, outputs: list[Path], kind: str):
        with self._counter_lock:
            self.new_manifest["items"][key] = self.manifest_item(source, outputs, kind)

    def manifest_item(self, source: Path, outputs: list[Path], kind: str) -> dict:
        stat_result = source.stat()
        return {
            "kind": kind,
            "source": source.resolve().as_posix(),
            "sha256": file_sha256(source),
            "size": stat_result.st_size,
            "signature": self.build_signature if kind != "copy" else "copy-v1",
            "outputs": [self.relative_output_path(output) for output in outputs],
        }

    def relative_output_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.output_dir).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def remove_stale_manifest_outputs(self):
        old_items = self.old_manifest.get("items", {})
        new_items = self.new_manifest.get("items", {})
        for key, item in old_items.items():
            if key in new_items:
                continue
            for rel_output in item.get("outputs", []):
                output = self.output_dir / rel_output
                try:
                    if output.is_file() or output.is_symlink():
                        output.unlink()
                        self.log(f"Remove stale {output}")
                except OSError as exc:
                    self.warn(f"Could not remove stale output {output}: {exc}")

    def compute_build_signature(self) -> str:
        template_roots = [
            Path(__file__).resolve().parent / "templates",
            *self.config.resolved_template_roots(),
        ]
        template_state = []
        for root in template_roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                stat_result = path.stat()
                template_state.append(
                    [
                        path.relative_to(root).as_posix(),
                        file_sha256(path),
                        stat_result.st_size,
                    ]
                )
        data = {
            "version": 1,
            "wiki_title": self.config.wiki_title,
            "mathjax_url": self.config.mathjax_url,
            "table_of_contents": self.config.table_of_contents,
            "generate_source_tags": self.config.generate_source_tags,
            "qcc_command": self.config.qcc_command,
            "artifact_base_url": self.config.artifact_base_url,
            "base_url": self.urls.base_url,
            "templates": template_state,
        }
        return json.dumps(data, sort_keys=True)

    def log(self, message: str):
        if not self.verbose:
            return
        with self._counter_lock:
            print(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StaticUrls:
    def __init__(self, base_url: str = ""):
        self.base_url = self.normalize_base_url(base_url)

    def normalize_base_url(self, base_url: str) -> str:
        if not base_url or base_url == "/":
            return ""
        parsed = urlsplit(base_url)
        if parsed.scheme and parsed.netloc:
            return base_url.rstrip("/")
        return f"/{base_url.strip('/')}"

    def front_url(self) -> str:
        return self.url("/")

    def index_url(self) -> str:
        return self.url("/_index.html")

    def recent_url(self) -> str:
        return self.url("/_recent.html")

    def search_url(self) -> str:
        return self.url("/_search.html")

    def search_index_url(self) -> str:
        return self.url("/search-index.json")

    def sitemap_url(self) -> str:
        return self.url("/sitemap.xml")

    def static_url(self) -> str:
        return self.url("/static/")

    def page_url(self, slug: str) -> str:
        if slug == "FrontPage":
            return self.front_url()
        return self.url(f"/{slug}.html")

    def wiki_asset_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/{repo.page_filename(slug)}")

    def sandbox_root_url(self) -> str:
        return self.sandbox_directory_url("")

    def sandbox_page_url(self, slug: str) -> str:
        return self.url(f"/sandbox/{slug}.html")

    def sandbox_asset_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/sandbox/{repo.page_filename(slug)}")

    def sandbox_directory_url(self, slug: str) -> str:
        if not slug:
            return self.url("/sandbox/")
        return self.url(f"/sandbox/{slug.strip('/')}/")

    def directory_url(self, slug: str) -> str:
        if not slug:
            return self.index_url()
        return self.url(f"/{slug.strip('/')}/")

    def history_url(self, slug: str) -> str:
        return self.url(f"/_history/{slug}.html")

    def raw_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/_raw/{repo.page_filename(slug)}")

    def sandbox_history_url(self, slug: str) -> str:
        return self.url(f"/_history/sandbox/{slug}.html")

    def sandbox_raw_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/_raw/sandbox/{repo.page_filename(slug)}")

    def source_root_url(self) -> str:
        return self.source_directory_url("")

    def source_directory_url(self, path: str) -> str:
        if not path:
            return self.url("/src/")
        return self.url(f"/src/{path.strip('/')}/")

    def source_page_url(self, path: str) -> str:
        return self.url(f"/src/{path.strip('/')}/")

    def source_asset_url(self, path: str) -> str:
        return self.url(f"/src/{path}")

    def page_output_path(self, output_dir: Path, slug: str) -> Path:
        if slug == "FrontPage":
            return output_dir / "index.html"
        return output_dir / self.local_path(f"{slug}.html")

    def directory_output_path(self, output_dir: Path, slug: str) -> Path:
        if not slug:
            return output_dir / "_directory.html"
        return output_dir / self.local_path(slug) / "index.html"

    def sandbox_page_output_path(self, output_dir: Path, slug: str) -> Path:
        return output_dir / "sandbox" / self.local_path(f"{slug}.html")

    def sandbox_directory_output_path(self, output_dir: Path, slug: str) -> Path:
        if not slug:
            return output_dir / "sandbox" / "index.html"
        return output_dir / "sandbox" / self.local_path(slug) / "index.html"

    def history_output_path(self, output_dir: Path, slug: str) -> Path:
        return output_dir / "_history" / self.local_path(f"{slug}.html")

    def raw_output_path(self, output_dir: Path, repo: WikiRepository, slug: str) -> Path:
        return output_dir / "_raw" / self.local_path(repo.page_filename(slug))

    def sandbox_history_output_path(self, output_dir: Path, slug: str) -> Path:
        return output_dir / "_history" / "sandbox" / self.local_path(f"{slug}.html")

    def sandbox_raw_output_path(self, output_dir: Path, repo: WikiRepository, slug: str) -> Path:
        return output_dir / "_raw" / "sandbox" / self.local_path(repo.page_filename(slug))

    def source_directory_output_path(self, output_dir: Path, path: str) -> Path:
        if not path:
            return output_dir / "src" / "index.html"
        return output_dir / "src" / self.local_path(path) / "index.html"

    def source_page_output_path(self, output_dir: Path, path: str) -> Path:
        return output_dir / "src" / self.local_path(path) / "index.html"

    def source_asset_output_path(self, output_dir: Path, path: str) -> Path:
        return output_dir / "src" / self.local_path(path)

    def url(self, path: str) -> str:
        if path == "/":
            return f"{self.base_url}/" if self.base_url else "/"
        return f"{self.base_url}{quote(path, safe='/._-~')}"

    def robots_path(self, path: str) -> str:
        parsed = urlsplit(self.url(path))
        return parsed.path or "/"

    def local_path(self, path: str) -> Path:
        return Path(PurePosixPath(path))


class TemplateRenderer:
    def __init__(self, override_roots: tuple[Path, ...] = ()):
        template_root = Path(__file__).resolve().parent / "templates"
        roots = [str(root) for root in override_roots if root.exists()]
        roots.append(str(template_root))
        self.environment = Environment(
            loader=FileSystemLoader(roots),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, template: str, context: dict) -> str:
        return self.environment.get_template(template).render(**context)


def title_for(slug: str) -> str:
    return slug.rsplit("/", 1)[-1].replace("_", " ")


def source_parent(slug: str) -> str:
    if not slug or "/" not in slug:
        return ""
    return slug.rsplit("/", 1)[0]


def artifact_rel_dir_for_slug(slug: str) -> str:
    return PurePosixPath(slug).with_suffix("").as_posix()


def artifact_key_dir_for_slug(slug: str, prefix: str = "") -> str:
    rel = artifact_rel_dir_for_slug(slug)
    if not prefix:
        return rel
    return (PurePosixPath(prefix.strip("/")) / rel).as_posix()


def prefixed_source_artifact_path(rel: str) -> str | None:
    normalized = normalized_relative_url_path(rel)
    if normalized is None:
        return None
    return (PurePosixPath("src") / normalized).as_posix()


def unique_urls(urls) -> list[str]:
    unique = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def normalized_relative_url_path(path: str) -> str | None:
    normalized = PurePosixPath(os.path.normpath(path.strip("/")))
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() == ".":
        return None
    return normalized.as_posix()


def normalized_source_href_path(path: str) -> str | None:
    normalized = PurePosixPath(os.path.normpath(path))
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() == ".":
        return None
    return normalized.as_posix()


def directory_parent(slug: str) -> str:
    if not slug or "/" not in slug:
        return ""
    return f"{slug.rsplit('/', 1)[0]}/"
