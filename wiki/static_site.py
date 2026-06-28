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

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gititpy.config import SiteConfig, default_config
from .darcsit import fenced_code, language_for_slug, render as render_darcsit, render_markdown
from .source import SourcePathError, SourceTree
from .storage import PageNameError, WikiRepository


HREF_RE = re.compile(r'\bhref=(["\'])([^"\']+)(["\'])')


@dataclass(frozen=True)
class StaticBuildResult:
    output_dir: Path
    html_files: int
    copied_files: int
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
        resolved_source_root = source_root if source_root is not None else self.config.resolved_source_root()
        self.source_tree = SourceTree(resolved_source_root) if resolved_source_root else None
        self.output_dir = Path(output_dir or self.config.base_dir / "public")
        self.urls = StaticUrls(base_url)
        self.templates = TemplateRenderer()
        self.html_files = 0
        self.copied_files = 0
        self.jobs = self.resolve_jobs(self.config.jobs)
        self._counter_lock = threading.Lock()
        self._warnings: list[str] = []

    def build(self, clean: bool = True) -> StaticBuildResult:
        self.repo.ensure_ready()
        if clean and self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.copy_static_assets()
        self.build_wiki()
        if self.source_tree is not None:
            self.build_source_tree()
        self.write_search_index()

        return StaticBuildResult(
            output_dir=self.output_dir,
            html_files=self.html_files,
            copied_files=self.copied_files,
            warnings=tuple(self._warnings),
        )

    def build_wiki(self):
        self.render_all_pages_index()
        self.render_recent_page()
        self.render_search_page()

        for directory in self.visible_directories(self.repo.root):
            if directory != self.repo.root:
                self.render_wiki_directory(directory)

        for path in self.visible_files(self.repo.root):
            rel_path = path.relative_to(self.repo.root)
            if self.is_renderable_wiki_file(path):
                self.render_wiki_page(self.repo.page_slug_for_path(rel_path))
            else:
                self.copy_file(path, self.output_dir / rel_path)

    def render_wiki_page(self, slug: str):
        source = self.repo.read_page(slug)
        source_path = self.repo.page_path(slug)
        content_html = render_darcsit(source, slug, source_path=source_path)
        context = self.page_context(slug) | {
            "content_html": self.rewrite_source_links(content_html),
            "revision": None,
            "source": source,
        }
        html = self.render_template("wiki/page.html", context)
        self.write_html(self.urls.page_output_path(self.output_dir, slug), html)

        if slug == "FrontPage":
            self.write_html(self.output_dir / "FrontPage.html", html)

        self.copy_raw_page(slug, source_path)
        self.render_history_page(slug)

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
        context = self.page_context(slug) | {"revisions": self.repo.history(slug)}
        self.write_html(
            self.urls.history_output_path(self.output_dir, slug),
            self.render_template("wiki/history.html", context),
        )

    def build_source_tree(self):
        if self.source_tree is None or not self.source_tree.root.exists():
            return

        for directory in self.visible_directories(self.source_tree.root):
            self.render_source_directory(directory)

        files = list(self.visible_files(self.source_tree.root))
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
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.copy_file(path, self.urls.source_asset_output_path(self.output_dir, rel))
                return
            content_html = self.render_source_content(source, rel, path)
            context = self.base_context() | {
                "page_title": f"/src/{rel}",
                "source_path": rel,
                "content_html": self.rewrite_source_links(content_html, current_source_path=rel),
            }
            self.write_html(
                self.urls.source_page_output_path(self.output_dir, rel),
                self.render_template("wiki/source_page.html", context),
            )
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
        path = self.output_dir / "search-index.json"
        path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        self.copied_files += 1

    def copy_raw_page(self, slug: str, source_path: Path):
        self.copy_file(source_path, self.urls.raw_output_path(self.output_dir, self.repo, slug))

    def copy_file(self, source: Path, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        with self._counter_lock:
            self.copied_files += 1

    def write_html(self, path: Path, html: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        with self._counter_lock:
            self.html_files += 1

    def render_template(self, template: str, context: dict) -> str:
        return self.templates.render(template, context)

    def render_source_content(self, source: str, rel: str, path: Path) -> str:
        try:
            return render_darcsit(source, rel, source_path=path)
        except RuntimeError as exc:
            language = language_for_slug(rel)
            if not language:
                raise
            self.warn(f"Rendered /src/{rel} as plain code after Darcsit failure: {exc}")
            return render_markdown(fenced_code(source, language))

    def rewrite_source_links(self, html: str, current_source_path: str | None = None) -> str:
        if self.source_tree is None:
            return html

        def replace(match: re.Match[str]) -> str:
            quote_char = match.group(1)
            href = match.group(2)
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
            rel = decoded.removeprefix("/src/")
        elif not decoded.startswith("/") and current_source_path:
            rel = (PurePosixPath(current_source_path).parent / decoded).as_posix()
        else:
            return None
        normalized = PurePosixPath(os.path.normpath(rel))
        if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() == ".":
            return None
        return normalized.as_posix()

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
            "edit_page_url": "",
            "history_page_url": self.urls.history_url(slug),
            "raw_page_url": self.urls.raw_url(self.repo, slug),
            "delete_page_url": "",
        }

    def wiki_entry_with_href(self, entry):
        if entry.is_dir:
            href = self.urls.directory_url(entry.slug.rstrip("/"))
        else:
            path = self.repo.page_path(entry.slug)
            href = self.urls.page_url(entry.slug) if self.is_renderable_wiki_file(path) else self.urls.wiki_asset_url(self.repo, entry.slug)
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
            if self.is_renderable_wiki_file(path):
                slugs.append(self.repo.page_slug_for_path(path.relative_to(self.repo.root)))
        return sorted(slugs, key=str.casefold)

    def is_renderable_wiki_file(self, path: Path) -> bool:
        slug = self.repo.page_slug_for_path(path.relative_to(self.repo.root))
        suffix = PurePosixPath(slug).suffix.lower()
        return path.suffix.lower() in {".md", ".page"} or suffix == ".bib" or language_for_slug(slug) is not None

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


class StaticUrls:
    def __init__(self, base_url: str = ""):
        self.base_url = self.normalize_base_url(base_url)

    def normalize_base_url(self, base_url: str) -> str:
        if not base_url or base_url == "/":
            return ""
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

    def static_url(self) -> str:
        return self.url("/static/")

    def page_url(self, slug: str) -> str:
        if slug == "FrontPage":
            return self.front_url()
        return self.url(f"/{slug}.html")

    def wiki_asset_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/{repo.page_filename(slug)}")

    def directory_url(self, slug: str) -> str:
        if not slug:
            return self.index_url()
        return self.url(f"/{slug.strip('/')}/")

    def history_url(self, slug: str) -> str:
        return self.url(f"/_history/{slug}.html")

    def raw_url(self, repo: WikiRepository, slug: str) -> str:
        return self.url(f"/_raw/{repo.page_filename(slug)}")

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

    def history_output_path(self, output_dir: Path, slug: str) -> Path:
        return output_dir / "_history" / self.local_path(f"{slug}.html")

    def raw_output_path(self, output_dir: Path, repo: WikiRepository, slug: str) -> Path:
        return output_dir / "_raw" / self.local_path(repo.page_filename(slug))

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

    def local_path(self, path: str) -> Path:
        return Path(PurePosixPath(path))


class TemplateRenderer:
    def __init__(self):
        template_root = Path(__file__).resolve().parent / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(template_root),
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


def directory_parent(slug: str) -> str:
    if not slug or "/" not in slug:
        return ""
    return f"{slug.rsplit('/', 1)[0]}/"
