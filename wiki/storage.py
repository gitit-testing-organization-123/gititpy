import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


DEFAULT_FRONT_PAGE = """# Welcome to GititPy!

This is a small Gitit-style static site. Pages are Markdown files stored in the
site source tree and rendered during the static build.

Edit this page with the `edit` tab, create links with regular Markdown like
`[Help](Help)`, or use wiki-style links like `[[Another Page]]`.
"""

DEFAULT_HELP_PAGE = """# Help

## Pages

Pages are written in Markdown. A page name maps to a Markdown file in the local
wiki page tree. For example, `FrontPage` is stored as `FrontPage.md`.
Pages with source-code extensions, such as `example.c` or `script.py`, are
stored under their exact names and rendered with Darcsit-style page magic when
documentation blocks are present.

## Links

Use regular Markdown links:

    [Front page](FrontPage)

You can also use simple wiki links:

    [[Another Page]]

## Static builds

Run the static build command to render the page tree into publishable HTML.
"""


class PageNameError(ValueError):
    pass


@dataclass(frozen=True)
class WikiEntry:
    name: str
    slug: str
    is_dir: bool


class WikiRepository:
    def __init__(self, root: Path, seed_defaults: bool = True):
        self.root = Path(root)
        self.seed_defaults = seed_defaults

    def ensure_ready(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.seed_defaults or self.has_page_files():
            return
        for slug, content in {
            "FrontPage": DEFAULT_FRONT_PAGE,
            "Help": DEFAULT_HELP_PAGE,
        }.items():
            path = self.page_path(slug)
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def has_page_files(self) -> bool:
        if not self.root.exists():
            return False
        for path in self.root.rglob("*"):
            if ".git" in path.parts:
                continue
            if path.is_file() and path.suffix in {".md", ".page"}:
                return True
        return False

    def normalize_slug(self, value: str | None) -> str:
        slug = (value or "FrontPage").strip().strip("/")
        if not slug:
            return "FrontPage"
        slug = re.sub(r"\s+", "_", slug)
        path = PurePosixPath(slug)
        if path.is_absolute() or ".." in path.parts:
            raise PageNameError("Page names cannot be absolute or contain '..'.")
        if any(part in {"", "."} or part.startswith(".") for part in path.parts):
            raise PageNameError("Page names cannot contain empty or hidden path parts.")
        if path.parts[0].startswith("_"):
            raise PageNameError("Page names cannot start with '_'.")
        return path.as_posix()

    def page_path(self, slug: str) -> Path:
        normalized = self.normalize_slug(slug)
        path = self.existing_page_path(slug, normalized) or self.root / self.page_filename(normalized)
        root = self.root.resolve()
        resolved_parent = path.parent.resolve()
        try:
            resolved_parent.relative_to(root)
        except ValueError as exc:
            raise PageNameError("Page path escapes the wiki root.") from exc
        return path

    def existing_page_path(self, slug: str, normalized: str) -> Path | None:
        candidates = []
        raw = (slug or "FrontPage").strip().strip("/")
        if raw:
            self.validate_path_parts(PurePosixPath(raw))
            if PurePosixPath(raw).suffix:
                candidates.append(self.root / raw)
            else:
                candidates.extend((self.root / f"{raw}.page", self.root / f"{raw}.md"))
        normalized_path = PurePosixPath(normalized)
        if normalized_path.suffix:
            candidates.append(self.root / normalized_path.as_posix())
        else:
            candidates.extend(
                (
                    self.root / f"{normalized_path.as_posix()}.page",
                    self.root / f"{normalized_path.as_posix()}.md",
                )
            )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def validate_path_parts(self, path: PurePosixPath):
        if path.is_absolute() or ".." in path.parts:
            raise PageNameError("Page names cannot be absolute or contain '..'.")
        if any(part in {"", "."} or part.startswith(".") for part in path.parts):
            raise PageNameError("Page names cannot contain empty or hidden path parts.")
        if path.parts and path.parts[0].startswith("_"):
            raise PageNameError("Page names cannot start with '_'.")

    def directory_path(self, slug: str) -> Path:
        if slug in {"", "."}:
            return self.root
        normalized = self.normalize_slug(slug)
        path = self.root / normalized
        root = self.root.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise PageNameError("Directory path escapes the wiki root.") from exc
        return path

    def page_filename(self, slug: str) -> str:
        path = PurePosixPath(slug)
        if path.suffix:
            return path.as_posix()
        return f"{path.as_posix()}.md"

    def read_page(self, slug: str) -> str:
        self.ensure_ready()
        path = self.page_path(slug)
        if not path.is_file():
            raise FileNotFoundError(slug)
        return path.read_text(encoding="utf-8")

    def write_page(self, slug: str, content: str):
        self.ensure_ready()
        normalized = self.normalize_slug(slug)
        path = self.page_path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def list_directory(self, slug: str) -> list[WikiEntry]:
        self.ensure_ready()
        directory = self.directory_path(slug)
        if not directory.is_dir():
            raise NotADirectoryError(slug)
        entries = []
        for child in directory.iterdir():
            if child.name.startswith("."):
                continue
            rel_path = child.relative_to(self.root)
            if child.is_dir():
                entries.append(WikiEntry(child.name, f"{rel_path.as_posix()}/", True))
                continue
            if not child.is_file():
                continue
            slug_path = self.page_slug_for_path(rel_path)
            entries.append(WikiEntry(PurePosixPath(slug_path).name, slug_path, False))
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.casefold()))

    def page_slug_for_path(self, path: PurePosixPath | Path) -> str:
        rel_path = PurePosixPath(path.as_posix())
        if rel_path.suffix in {".md", ".page"}:
            rel_path = rel_path.with_suffix("")
        return rel_path.as_posix()
