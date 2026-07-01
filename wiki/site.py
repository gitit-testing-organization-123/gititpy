import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.sax.saxutils import escape as xml_escape

from gititpy.config import SiteConfig, default_config
from .darcsit import fenced_code, language_for_slug, render as render_darcsit, render_markdown
from .source import SourcePathError, SourceTree
from .storage import PageNameError, WikiRepository
from .helpers import (
    artifact_key_dir_for_slug,
    directory_parent,
    file_sha256,
    normalized_relative_url_path,
    normalized_source_href_path,
    prefixed_source_artifact_path,
    source_parent,
    title_for,
    unique_urls,
)
from .templates import TemplateRenderer
from .types import StaticBuildResult, StaticTree, StaticTreeJob
from .urls import StaticUrls
from .tags import generate_qcc_tags, is_qcc_tags_file


ATTRIBUTE_VALUE_RE = r""""([^"]*)"|'([^']*)'|([^\s"'=<>`]+)"""
HREF_RE = re.compile(r"\bhref=(?:" + ATTRIBUTE_VALUE_RE + r")")
URL_ATTR_RE = re.compile(r"\b(href|src|poster)=(?:" + ATTRIBUTE_VALUE_RE + r")")
TEMP_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9_.+-]+\.[A-Za-z0-9]+$")


class StaticSiteBuilder:
    def __init__(
        self,
        wiki_root: Path | None = None,
        source_root: Path | None = None,
        output_dir: Path | None = None,
        base_url: str | None = None,
        config: SiteConfig | None = None,
    ):
        self.config = config or default_config()
        self.html_files = 0
        self.copied_files = 0
        self.skipped_files = 0
        self._counter_lock = threading.Lock()
        self._warnings: list[str] = []
        self._warning_keys: set[str] = set()
        self.repo = WikiRepository(wiki_root or self.config.resolved_wiki_root())
        self.wiki_tree = StaticTree(
            name="wiki",
            root=self.repo.root,
            url_prefix="",
            output_prefix="",
            label="wiki tree",
            nav_label="Wiki",
            kind="wiki",
            manifest_page_prefix="wiki",
            manifest_page_kind="page",
            search_indexed=True,
        )
        resolved_source_root = source_root if source_root is not None else self.config.resolved_source_root()
        source_tree = self.make_source_tree(
            name="source",
            prefix="src",
            label="source tree",
            nav_label="Source code",
            root=resolved_source_root,
            manifest_page_prefix="source-page",
            manifest_page_kind="source-page",
            warn_missing=False,
            artifact_roots=self.source_artifact_roots(),
        )
        sandbox_tree = self.make_source_tree(
            name="sandbox",
            prefix="sandbox",
            label="sandbox tree",
            nav_label="Sandbox",
            root=self.config.resolved_sandbox_root(),
            manifest_page_prefix="sandbox-source-page",
            manifest_page_kind="sandbox-source-page",
            warn_missing=self.config.sandbox_root is not None,
            artifact_roots=self.sandbox_artifact_roots(),
        )
        self.source_tree = source_tree.tree if source_tree is not None else None
        self.source_trees = tuple(
            tree for tree in (source_tree, sandbox_tree) if tree is not None
        )
        self.static_trees = (self.wiki_tree, *self.source_trees)
        self.output_dir = Path(output_dir) if output_dir is not None else self.config.resolved_output_dir()
        self.urls = StaticUrls(self.config.base_url if base_url is None else base_url)
        self.templates = TemplateRenderer(self.config.resolved_template_roots())
        self.jobs = self.resolve_jobs(self.config.jobs)
        self.verbose = self.config.verbose
        self.manifest_path = self.output_dir / ".gititpy-build.json"
        self.force_rebuild = False
        self.old_manifest: dict = {"items": {}}
        self.new_manifest: dict = {"version": 1, "items": {}}
        self.build_signature = self.compute_build_signature()

    def make_source_tree(
        self,
        name: str,
        prefix: str,
        label: str,
        nav_label: str,
        root: Path | None,
        manifest_page_prefix: str,
        manifest_page_kind: str,
        warn_missing: bool,
        artifact_roots: tuple[Path, ...] = (),
    ) -> StaticTree | None:
        if root is None:
            return None
        root = Path(root)
        if not root.exists():
            if warn_missing:
                self.warn(f"Configured {label} does not exist: {root}")
            return None
        return StaticTree(
            name=name,
            root=root,
            url_prefix=prefix,
            output_prefix=prefix,
            label=label,
            nav_label=nav_label,
            kind="source",
            manifest_page_prefix=manifest_page_prefix,
            manifest_page_kind=manifest_page_kind,
            search_indexed=True,
            generates_tags=True,
            artifact_prefix=prefix,
            artifact_roots=artifact_roots,
            source_tree=SourceTree(root),
        )

    def source_artifact_roots(self) -> tuple[Path, ...]:
        roots = []
        configured_root = self.config.resolved_artifact_root()
        if configured_root is not None:
            roots.append(configured_root)
        default_root = self.config.base_dir / "basilisk" / "build" / "release" / "src"
        if default_root.exists():
            roots.append(default_root)
        return tuple(roots)

    def sandbox_artifact_roots(self) -> tuple[Path, ...]:
        configured_root = self.config.resolved_sandbox_artifact_root()
        return (configured_root,) if configured_root is not None else ()

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
        self.log("Rendering utility pages")
        self.render_all_pages_index()
        self.render_search_page()
        for tree in self.static_trees:
            self.log(f"Rendering {tree.label} {tree.root} with {self.jobs} job(s)")
            self.build_tree(tree)
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

    def build_tree(self, tree: StaticTree):
        if not tree.root.exists():
            return
        jobs = self.tree_jobs(tree)
        directory_jobs = [job for job in jobs if job.action == "render-directory"]
        file_jobs = [job for job in jobs if job.action != "render-directory"]
        self.log(f"Rendering {len(directory_jobs)} {tree.name} directories")
        for job in directory_jobs:
            self.render_tree_job(job)
        self.log(f"Rendering/copying {len(file_jobs)} {tree.name} file(s)")
        if tree.kind == "wiki" or self.jobs == 1:
            for job in file_jobs:
                self.render_tree_job(job)
            return
        with ThreadPoolExecutor(max_workers=self.jobs) as executor:
            futures = [executor.submit(self.render_tree_job, job) for job in file_jobs]
            for future in as_completed(futures):
                future.result()

    def tree_jobs(self, tree: StaticTree) -> list[StaticTreeJob]:
        jobs = []
        for directory in self.tree_directories(tree):
            jobs.append(StaticTreeJob("render-directory", tree, self.tree_rel_path(tree, directory), directory))
        for path in self.tree_files(tree):
            action = "render-page" if self.should_render_tree_file(tree, path) else "copy-asset"
            rel = self.tree_page_rel_path(tree, path) if action == "render-page" else self.tree_rel_path(tree, path)
            jobs.append(StaticTreeJob(action, tree, rel, path))
        return jobs

    def render_tree_job(self, job: StaticTreeJob):
        if job.action == "render-directory":
            self.render_tree_directory(job.tree, job.rel_path)
        elif job.action == "render-page":
            self.render_tree_page(job.tree, job.rel_path)
        elif job.source_path is not None:
            self.copy_tree_asset(job.tree, job.rel_path, job.source_path)

    def render_tree_directory(self, tree: StaticTree, rel_path: str):
        if tree.kind == "wiki":
            self.render_wiki_directory_path(rel_path)
        else:
            self.render_source_tree_directory_path(tree, rel_path)

    def render_tree_page(self, tree: StaticTree, rel_path: str):
        if tree.kind == "wiki":
            self.render_wiki_page(rel_path)
        else:
            path = tree.tree.resolve(rel_path)
            self.render_source_tree_file(tree, path)

    def copy_tree_asset(self, tree: StaticTree, rel_path: str, source_path: Path):
        if tree.kind == "wiki":
            self.copy_file(source_path, self.output_dir / self.urls.local_path(rel_path))
        else:
            self.copy_file(source_path, self.urls.tree_asset_output_path(self.output_dir, tree.output_prefix, rel_path))

    def tree_directories(self, tree: StaticTree):
        for directory in self.visible_directories(tree.root):
            if tree.kind == "wiki":
                if directory == tree.root or self.is_shadowed_wiki_source_path(directory):
                    continue
                rel = directory.relative_to(tree.root).as_posix()
                if self.renderable_wiki_page_exists(rel):
                    continue
            yield directory

    def tree_files(self, tree: StaticTree):
        for path in self.visible_files(tree.root):
            if tree.kind == "wiki" and self.is_shadowed_wiki_source_path(path):
                continue
            if tree.kind == "source" and is_qcc_tags_file(path):
                continue
            yield path

    def tree_rel_path(self, tree: StaticTree, path: Path) -> str:
        rel = path.relative_to(tree.root).as_posix()
        return "" if rel == "." else rel

    def tree_page_rel_path(self, tree: StaticTree, path: Path) -> str:
        if tree.kind == "wiki":
            return self.repo.page_slug_for_path(path.relative_to(self.repo.root))
        return self.tree_rel_path(tree, path)

    def should_render_tree_file(self, tree: StaticTree, path: Path) -> bool:
        if tree.kind == "wiki":
            return self.is_renderable_wiki_file(path)
        return self.will_render_source_path(tree, path)

    def render_wiki_page(self, slug: str):
        source = self.repo.read_page(slug)
        source_path = self.repo.page_path(slug)
        output_paths = [
            self.urls.page_output_path(self.output_dir, slug),
        ]
        manifest_key = f"wiki:{slug}"
        if self.is_manifest_current(manifest_key, source_path, output_paths, "page"):
            self.record_manifest_item(manifest_key, source_path, output_paths, "page")
            self.skip_file(f"Skip wiki page {slug}")
            return
        content_html = self.render_tree_content(
            source,
            slug,
            source_path,
            display_path=slug,
        )
        context = self.page_context(slug) | {
            "content_html": self.rewrite_content_links(content_html, current_wiki_slug=slug),
            "source": source,
        }
        html = self.render_template("wiki/page.html", context)
        self.write_html(self.urls.page_output_path(self.output_dir, slug), html)

        self.record_manifest_item(manifest_key, source_path, output_paths, "page")

    def render_wiki_directory_path(self, rel_slug: str):
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

    def render_all_pages_index(self):
        pages = [
            SimpleNamespace(label=slug, href=self.urls.page_url(slug))
            for slug in self.renderable_wiki_slugs()
        ]
        context = self.base_context() | {"pages": pages, "page_title": "All pages"}
        self.write_html(self.output_dir / "_index.html", self.render_template("wiki/index.html", context))

    def render_search_page(self):
        context = self.base_context() | {
            "page_title": "Search",
            "query": "",
            "results": [],
            "search_index_url": self.urls.search_index_url(),
        }
        self.write_html(self.output_dir / "_search.html", self.render_template("wiki/search.html", context))

    def render_source_tree_file(self, browser: StaticTree, path: Path):
        rel = path.relative_to(browser.tree.root).as_posix()
        if self.will_render_source_path(browser, path):
            output_path = self.urls.tree_page_output_path(self.output_dir, browser.output_prefix, rel)
            manifest_key = f"{browser.manifest_page_prefix}:{rel}"
            if self.is_manifest_current(manifest_key, path, [output_path], browser.manifest_page_kind):
                self.record_manifest_item(manifest_key, path, [output_path], browser.manifest_page_kind)
                self.skip_file(f"Skip source page /{browser.prefix}/{rel}")
                return
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.copy_file(path, self.urls.tree_asset_output_path(self.output_dir, browser.output_prefix, rel))
                return
            self.generate_source_tags(browser, path)
            content_html = self.render_tree_content(
                source,
                rel,
                path,
                basilisk_root=browser.tree.root,
                display_path=f"/{browser.prefix}/{rel}",
            )
            context = self.base_context() | {
                "page_title": f"/{browser.prefix}/{rel}",
                "canonical_url": self.urls.tree_page_url(browser.url_prefix, rel),
                "source_path": rel,
                "content_html": self.rewrite_content_links(
                    content_html,
                    current_source_path=rel,
                    current_source_tree=browser,
                    current_artifact_rel_dir=artifact_key_dir_for_slug(rel, prefix=browser.prefix),
                ),
            }
            self.write_html(
                output_path,
                self.render_template("wiki/source_page.html", context),
            )
            self.record_manifest_item(manifest_key, path, [output_path], browser.manifest_page_kind)
        else:
            self.copy_file(path, self.urls.tree_asset_output_path(self.output_dir, browser.output_prefix, rel))

    def render_source_tree_directory_path(self, browser: StaticTree, rel: str):
        try:
            entries = browser.tree.entries(rel)
        except (NotADirectoryError, SourcePathError):
            return
        entries = [self.source_entry_with_href(browser, entry) for entry in entries]
        parent = source_parent(rel)
        output_path = self.urls.tree_directory_output_path(self.output_dir, browser.output_prefix, rel)
        context = self.base_context() | {
            "page_title": f"/{browser.prefix}/{rel}" if rel else f"/{browser.prefix}",
            "canonical_url": self.urls.tree_directory_url(browser.url_prefix, rel),
            "source_path": rel,
            "tree_root_url": self.urls.tree_directory_url(browser.url_prefix, ""),
            "parent_path": parent,
            "parent_url": self.urls.tree_directory_url(browser.url_prefix, parent)
            if parent
            else self.urls.tree_directory_url(browser.url_prefix, ""),
            "entries": entries,
        }
        self.write_html(
            output_path,
            self.render_template("wiki/source_index.html", context),
        )
        self.record_generated_item(f"{browser.name}-directory:{rel}", [output_path], f"{browser.name}-directory")

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
        for tree in self.static_trees:
            if tree.search_indexed:
                documents.extend(self.tree_search_documents(tree))
        path = self.output_dir / "search-index.json"
        path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        self.log(f"Write {path}")
        self.copied_files += 1

    def tree_search_documents(self, tree: StaticTree) -> list[dict[str, str]]:
        if tree.kind == "wiki":
            return self.wiki_search_documents()
        return self.source_tree_search_documents(tree)

    def wiki_search_documents(self) -> list[dict[str, str]]:
        documents = []
        for slug in self.renderable_wiki_slugs():
            try:
                text = self.repo.read_page(slug)
            except FileNotFoundError:
                continue
            documents.append({"title": slug, "url": self.urls.page_url(slug), "text": text})
        for slug in self.renderable_wiki_directory_slugs():
            documents.append(
                {
                    "title": f"{slug}/",
                    "url": self.urls.directory_url(slug),
                    "text": self.wiki_directory_search_text(slug),
                }
            )
        return documents

    def wiki_directory_search_text(self, slug: str) -> str:
        try:
            entries = self.repo.list_directory(slug)
        except (NotADirectoryError, PageNameError):
            return ""
        return "\n".join(entry.name for entry in entries)

    def source_tree_search_documents(self, browser: StaticTree) -> list[dict[str, str]]:
        documents = []
        for rel in self.renderable_source_directory_paths(browser):
            documents.append(
                {
                    "title": f"/{browser.prefix}/{rel}/",
                    "url": self.urls.tree_directory_url(browser.url_prefix, rel),
                    "text": self.source_tree_directory_search_text(browser, rel),
                }
            )
        if browser.tree.root.exists():
            documents.append(
                {
                    "title": f"/{browser.prefix}/",
                    "url": self.urls.tree_directory_url(browser.url_prefix, ""),
                    "text": self.source_tree_directory_search_text(browser, ""),
                }
            )
        for rel in self.renderable_source_file_paths(browser):
            path = browser.tree.resolve(rel)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            documents.append(
                {
                    "title": f"/{browser.prefix}/{rel}",
                    "url": self.urls.tree_page_url(browser.url_prefix, rel),
                    "text": text,
                }
            )
        return documents

    def source_tree_directory_search_text(self, browser: StaticTree, rel: str) -> str:
        try:
            entries = browser.tree.entries(rel)
        except (NotADirectoryError, SourcePathError):
            return ""
        return "\n".join(entry.name for entry in entries)

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
        urls = []
        for tree in self.static_trees:
            urls.extend(self.tree_sitemap_urls(tree))

        return sorted(unique_urls(urls), key=str.casefold)

    def tree_sitemap_urls(self, tree: StaticTree) -> list[str]:
        if tree.kind == "wiki":
            urls = [self.urls.front_url()]
            urls.extend(self.urls.page_url(slug) for slug in self.renderable_wiki_slugs() if slug != "FrontPage")
            urls.extend(self.urls.directory_url(slug) for slug in self.renderable_wiki_directory_slugs())
            return urls
        if not tree.root.exists():
            return []
        urls = [self.urls.tree_directory_url(tree.url_prefix, "")]
        urls.extend(
            self.urls.tree_directory_url(tree.url_prefix, path)
            for path in self.renderable_source_directory_paths(tree)
        )
        urls.extend(
            self.urls.tree_page_url(tree.url_prefix, path)
            for path in self.renderable_source_file_paths(tree)
        )
        return urls

    def write_robots_txt(self):
        lines = [
            "User-agent: *",
            f"Allow: {self.urls.robots_path('/')}",
            f"Disallow: {self.urls.robots_path('/_search.html')}",
            "",
            f"Sitemap: {self.urls.sitemap_url()}",
            "",
        ]
        path = self.output_dir / "robots.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.log(f"Write {path}")
        self.copied_files += 1

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

    def render_tree_content(
        self,
        source: str,
        slug: str,
        path: Path,
        basilisk_root: Path | None = None,
        display_path: str | None = None,
    ) -> str:
        try:
            return render_darcsit(
                source,
                slug,
                source_path=path,
                table_of_contents=self.config.table_of_contents,
                basilisk_root=basilisk_root,
            )
        except RuntimeError as exc:
            language = language_for_slug(slug)
            if not language:
                raise
            self.warn(f"Rendered {display_path or slug} as plain code after Darcsit failure: {exc}")
            return render_markdown(fenced_code(source, language), table_of_contents=False)

    def rewrite_content_links(
        self,
        html: str,
        current_wiki_slug: str | None = None,
        current_source_path: str | None = None,
        current_source_tree: StaticTree | None = None,
        current_artifact_rel_dir: str | None = None,
    ) -> str:
        html = self.rewrite_wiki_links(html, current_wiki_slug=current_wiki_slug)
        html = self.rewrite_source_links(
            html,
            current_source_path=current_source_path,
            current_source_tree=current_source_tree,
        )
        return self.rewrite_artifact_links(html, current_artifact_rel_dir=current_artifact_rel_dir)

    def rewrite_wiki_links(self, html: str, current_wiki_slug: str | None = None) -> str:
        if current_wiki_slug is None:
            return html

        def replace(match: re.Match[str]) -> str:
            quote_char = '"' if match.group(1) is not None else "'" if match.group(2) is not None else '"'
            href = match.group(1) or match.group(2) or match.group(3)
            rewritten = self.rewrite_wiki_href(href, current_wiki_slug=current_wiki_slug)
            return f"href={quote_char}{rewritten}{quote_char}"

        return HREF_RE.sub(replace, html)

    def rewrite_wiki_href(self, href: str, current_wiki_slug: str) -> str:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
            return href
        target = self.wiki_target_for_href_path(parsed.path, current_wiki_slug)
        if target is None:
            return href
        rewritten = urlsplit(self.urls.page_url(target))
        return urlunsplit(("", "", rewritten.path, parsed.query, parsed.fragment))

    def wiki_target_for_href_path(self, path: str, current_wiki_slug: str) -> str | None:
        decoded = unquote(path)
        candidates = []
        stripped_absolute = decoded.strip("/") if decoded.startswith("/") else None
        if stripped_absolute:
            candidates.append(stripped_absolute)
        elif not decoded.startswith("/"):
            parent = PurePosixPath(current_wiki_slug).parent
            if parent.as_posix() == ".":
                parent = PurePosixPath("")
            candidates.append((parent / decoded).as_posix())
            candidates.append(decoded)
        for candidate in candidates:
            slug = self.normalized_wiki_link_slug(candidate)
            if slug is not None and self.renderable_wiki_page_exists(slug):
                return slug
        return None

    def normalized_wiki_link_slug(self, path: str) -> str | None:
        normalized = normalized_source_href_path(path)
        if normalized is None:
            return None
        if normalized.endswith(".html"):
            normalized = normalized[:-5]
        return normalized or None

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
        if not rel_parts or len(rel_parts) == 1:
            return None
        if rel_parts[0] == artifact_stem:
            return (PurePosixPath(current_artifact_rel_dir) / PurePosixPath(*rel_parts[1:])).as_posix()

        sibling_artifact = self.sibling_artifact_path(rel_path, current_artifact_rel_dir)
        if sibling_artifact is not None:
            return sibling_artifact
        return None

    def sibling_artifact_path(self, rel_path: str, current_artifact_rel_dir: str) -> str | None:
        current_dir = PurePosixPath(current_artifact_rel_dir)
        if not current_dir.parts or self.source_tree_for_prefix(current_dir.parts[0]) is None:
            return None
        candidate = current_dir.parent / PurePosixPath(rel_path)
        candidate_path = candidate.as_posix()
        if self.artifact_exists(candidate_path):
            return candidate_path
        return None

    def artifact_exists(self, artifact_path: str) -> bool:
        normalized = normalized_relative_url_path(artifact_path)
        if normalized is None:
            return False
        parts = PurePosixPath(normalized).parts
        if not parts:
            return False
        tree = self.source_tree_for_prefix(parts[0])
        if tree is None:
            return False
        rel = Path(*parts[1:])

        for root in tree.artifact_roots:
            candidate = root / rel
            if candidate.is_file() or candidate.is_symlink():
                return True

        candidate = tree.tree.root / rel
        if candidate.is_file() or candidate.is_symlink():
            return True

        return False

    def temp_plot_artifact_path(self, path: str, current_artifact_rel_dir: str) -> str | None:
        if not path.startswith("/tmp/"):
            return None
        name = PurePosixPath(path).name
        if not TEMP_ARTIFACT_RE.match(name):
            return None
        return (PurePosixPath(current_artifact_rel_dir) / name).as_posix()

    def absolute_source_artifact_path(self, path: str) -> str | None:
        if not path.startswith("/"):
            return None
        normalized_path = Path(os.path.normpath(path))
        for browser in self.source_trees:
            try:
                source_rel = normalized_path.relative_to(browser.tree.root.resolve())
            except ValueError:
                continue
            return prefixed_source_artifact_path(browser.prefix, source_rel.as_posix())
        return None

    def generate_source_tags(self, browser: StaticTree, path: Path):
        if not self.config.generate_source_tags:
            return
        result = generate_qcc_tags(path, browser.tree.root, self.config.qcc_command)
        if result.warning:
            if "command not found" in result.warning:
                self.warn_once(f"qcc-missing:{self.config.qcc_command}", result.warning)
            else:
                self.warn(result.warning)
        elif result.generated:
            self.log(f"Generate qcc tags {path}.tags")

    def rewrite_source_links(
        self,
        html: str,
        current_source_path: str | None = None,
        current_source_tree: StaticTree | None = None,
    ) -> str:
        if not self.source_trees:
            return html

        def replace(match: re.Match[str]) -> str:
            quote_char = '"' if match.group(1) is not None else "'" if match.group(2) is not None else '"'
            href = match.group(1) or match.group(2) or match.group(3)
            rewritten = self.rewrite_source_href(
                href,
                current_source_path=current_source_path,
                current_source_tree=current_source_tree,
            )
            return f"href={quote_char}{rewritten}{quote_char}"

        return HREF_RE.sub(replace, html)

    def rewrite_source_href(
        self,
        href: str,
        current_source_path: str | None = None,
        current_source_tree: StaticTree | None = None,
    ) -> str:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
            return href
        if parsed.path.endswith("/"):
            return href

        target = self.source_target_for_href_path(
            parsed.path,
            current_source_path=current_source_path,
            current_source_tree=current_source_tree,
        )
        if target is None:
            return href
        browser, rel = target
        if not self.is_renderable_source_rel(browser, rel):
            return href

        rewritten = urlsplit(self.urls.tree_page_url(browser.url_prefix, rel))
        return urlunsplit(("", "", rewritten.path, parsed.query, parsed.fragment))

    def source_target_for_href_path(
        self,
        path: str,
        current_source_path: str | None = None,
        current_source_tree: StaticTree | None = None,
    ) -> tuple[StaticTree, str] | None:
        decoded = unquote(path)
        for browser in self.source_trees:
            prefix = f"/{browser.prefix}/"
            if decoded.startswith(prefix):
                rel = normalized_source_href_path(decoded.removeprefix(prefix))
                return (browser, rel) if rel is not None else None
        if not decoded.startswith("/") and current_source_path and current_source_tree is not None:
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
                if self.is_renderable_source_rel(current_source_tree, rel):
                    return current_source_tree, rel
            if fallback is not None:
                return current_source_tree, fallback
        return None

    def is_renderable_source_rel(self, browser: StaticTree, rel: str) -> bool:
        try:
            path = browser.tree.resolve(rel)
        except SourcePathError:
            return False
        return path.is_file() and self.will_render_source_path(browser, path)

    def base_context(self) -> dict:
        return {
            "wiki_title": self.config.wiki_title,
            "canonical_url": None,
            "front_url": self.urls.front_url(),
            "all_pages_url": self.urls.index_url(),
            "source_tree_links": [
                SimpleNamespace(label=tree.nav_label, href=self.urls.tree_directory_url(tree.url_prefix, ""))
                for tree in self.source_trees
            ],
            "search_url": self.urls.search_url(),
            "go_url": self.urls.search_url(),
            "help_url": self.urls.page_url("Help"),
            "mathjax_url": self.config.mathjax_url,
            "static_url": self.urls.static_url(),
        }

    def page_context(self, slug: str) -> dict:
        return self.base_context() | {
            "page_slug": slug,
            "page_title": title_for(slug),
            "page_url": self.urls.page_url(slug),
            "canonical_url": self.urls.page_url(slug),
        }

    def wiki_entry_with_href(self, entry):
        if entry.is_dir:
            slug = entry.slug.rstrip("/")
            href = self.urls.page_url(slug) if self.renderable_wiki_page_exists(slug) else self.urls.directory_url(slug)
        else:
            path = self.repo.page_path(entry.slug)
            href = self.urls.page_url(entry.slug) if self.is_renderable_wiki_file(path) else self.urls.wiki_asset_url(self.repo, entry.slug)
        return SimpleNamespace(name=entry.name, slug=entry.slug, is_dir=entry.is_dir, href=href)

    def source_entry_with_href(self, browser: StaticTree, entry):
        if entry.is_dir:
            href = self.urls.tree_directory_url(browser.url_prefix, entry.path)
        else:
            path = browser.tree.resolve(entry.path)
            href = (
                self.urls.tree_page_url(browser.url_prefix, entry.path)
                if self.will_render_source_path(browser, path)
                else self.urls.tree_asset_url(browser.url_prefix, entry.path)
            )
        return SimpleNamespace(name=entry.name, path=entry.path, is_dir=entry.is_dir, href=href)

    def renderable_wiki_slugs(self) -> list[str]:
        slugs = []
        for path in self.visible_files(self.repo.root):
            if self.is_shadowed_wiki_source_path(path):
                continue
            if self.is_renderable_wiki_file(path):
                slugs.append(self.repo.page_slug_for_path(path.relative_to(self.repo.root)))
        return sorted(slugs, key=str.casefold)

    def renderable_wiki_directory_slugs(self) -> list[str]:
        slugs = []
        for path in self.visible_directories(self.repo.root):
            if path == self.repo.root or self.is_shadowed_wiki_source_path(path):
                continue
            slug = path.relative_to(self.repo.root).as_posix()
            if self.renderable_wiki_page_exists(slug):
                continue
            slugs.append(slug)
        return sorted(slugs, key=str.casefold)

    def renderable_source_directory_paths(self, browser: StaticTree) -> list[str]:
        if not browser.tree.root.exists():
            return []
        paths = []
        for path in self.visible_directories(browser.tree.root):
            if path == browser.tree.root:
                continue
            paths.append(path.relative_to(browser.tree.root).as_posix())
        return sorted(paths, key=str.casefold)

    def renderable_source_file_paths(self, browser: StaticTree) -> list[str]:
        if not browser.tree.root.exists():
            return []
        paths = []
        for path in self.visible_files(browser.tree.root):
            if is_qcc_tags_file(path) or not self.will_render_source_path(browser, path):
                continue
            paths.append(path.relative_to(browser.tree.root).as_posix())
        return sorted(paths, key=str.casefold)

    def is_renderable_wiki_file(self, path: Path) -> bool:
        slug = self.repo.page_slug_for_path(path.relative_to(self.repo.root))
        suffix = PurePosixPath(slug).suffix.lower()
        if path.suffix.lower() in {".md", ".page"} or suffix == ".bib":
            return True
        if language_for_slug(slug) is not None:
            return True
        return SourceTree(path.parent).should_render(path)

    def renderable_wiki_page_exists(self, slug: str) -> bool:
        try:
            path = self.repo.page_path(slug)
        except PageNameError:
            return False
        return path.is_file() and self.is_renderable_wiki_file(path)

    def is_shadowed_wiki_source_path(self, path: Path) -> bool:
        rel = path.relative_to(self.repo.root)
        source_prefixes = {tree.url_prefix for tree in self.source_trees if tree.url_prefix}
        return bool(rel.parts) and rel.parts[0] in source_prefixes

    def will_render_source_path(self, browser: StaticTree, path: Path) -> bool:
        if not browser.tree.should_render(path):
            return False
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False
        return True

    def source_tree_for_prefix(self, prefix: str) -> StaticTree | None:
        for browser in self.source_trees:
            if browser.prefix == prefix:
                return browser
        return None

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

    def record_generated_item(self, key: str, outputs: list[Path], kind: str):
        with self._counter_lock:
            self.new_manifest["items"][key] = {
                "kind": kind,
                "signature": self.build_signature,
                "outputs": [self.relative_output_path(output) for output in outputs],
            }

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
            new_outputs = set(new_items.get(key, {}).get("outputs", []))
            for rel_output in item.get("outputs", []):
                if rel_output in new_outputs:
                    continue
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
