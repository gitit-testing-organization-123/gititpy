import re
import subprocess
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
class Revision:
    commit: str
    short_commit: str
    author: str
    date: str
    subject: str


@dataclass(frozen=True)
class WikiEntry:
    name: str
    slug: str
    is_dir: bool


class WikiRepository:
    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure_ready(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for slug, content in {
            "FrontPage": DEFAULT_FRONT_PAGE,
            "Help": DEFAULT_HELP_PAGE,
        }.items():
            path = self.page_path(slug)
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def has_git_history(self) -> bool:
        return (self.root / ".git").exists()

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
        path = self.root / self.page_filename(normalized)
        root = self.root.resolve()
        resolved_parent = path.parent.resolve()
        try:
            resolved_parent.relative_to(root)
        except ValueError as exc:
            raise PageNameError("Page path escapes the wiki root.") from exc
        return path

    def directory_path(self, slug: str) -> Path:
        normalized = self.normalize_slug(slug)
        path = self.root / normalized
        root = self.root.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise PageNameError("Directory path escapes the wiki root.") from exc
        return path

    def relative_page_path(self, slug: str) -> str:
        return self.page_filename(self.normalize_slug(slug))

    def page_filename(self, slug: str) -> str:
        path = PurePosixPath(slug)
        if path.suffix:
            return path.as_posix()
        return f"{path.as_posix()}.md"

    def exists(self, slug: str) -> bool:
        self.ensure_ready()
        return self.page_path(slug).is_file()

    def directory_exists(self, slug: str) -> bool:
        self.ensure_ready()
        return self.directory_path(slug).is_dir()

    def read_page(self, slug: str, revision: str | None = None) -> str:
        self.ensure_ready()
        if revision:
            if not self.has_git_history():
                raise FileNotFoundError(slug)
            result = self._git("show", f"{revision}:{self.relative_page_path(slug)}", check=False)
            if result.returncode != 0:
                raise FileNotFoundError(slug)
            return result.stdout
        path = self.page_path(slug)
        if not path.is_file():
            raise FileNotFoundError(slug)
        return path.read_text(encoding="utf-8")

    def write_page(self, slug: str, content: str, message: str | None = None):
        self.ensure_ready()
        normalized = self.normalize_slug(slug)
        path = self.page_path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete_page(self, slug: str, message: str | None = None):
        self.ensure_ready()
        normalized = self.normalize_slug(slug)
        path = self.page_path(normalized)
        if path.exists():
            path.unlink()

    def list_pages(self) -> list[str]:
        self.ensure_ready()
        pages = []
        for path in self.root.rglob("*"):
            if ".git" in path.parts:
                continue
            if not path.is_file():
                continue
            rel_path = path.relative_to(self.root)
            if rel_path.suffix in {".md", ".page"}:
                rel_path = rel_path.with_suffix("")
            rel = rel_path
            pages.append(rel.as_posix())
        return sorted(pages, key=str.casefold)

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

    def search(self, query: str) -> list[dict[str, str]]:
        self.ensure_ready()
        needle = query.casefold()
        if not needle:
            return []
        results = []
        for slug in self.list_pages():
            text = self.read_page(slug)
            haystack = text.casefold()
            if needle not in haystack:
                continue
            snippet = self._snippet(text, needle)
            results.append({"slug": slug, "snippet": snippet})
        return results

    def history(self, slug: str) -> list[Revision]:
        self.ensure_ready()
        if not self.has_git_history():
            return []
        result = self._git(
            "log",
            "--follow",
            "--date=short",
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s",
            "--",
            self.relative_page_path(slug),
            check=False,
        )
        return self._parse_revisions(result.stdout)

    def recent(self, limit: int = 30) -> list[Revision]:
        self.ensure_ready()
        if not self.has_git_history():
            return []
        result = self._git(
            "log",
            f"-n{limit}",
            "--date=short",
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s",
            check=False,
        )
        return self._parse_revisions(result.stdout)

    def commit_all(self, message: str):
        if not self.has_git_history():
            return
        self._git("add", "--all")
        diff = self._git("diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            return
        self._git(
            "-c",
            "user.name=GititPy",
            "-c",
            "user.email=gititpy@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def _parse_revisions(self, output: str) -> list[Revision]:
        revisions = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f", 4)
            if len(parts) != 5:
                continue
            revisions.append(Revision(*parts))
        return revisions

    def _snippet(self, text: str, needle: str) -> str:
        for line in text.splitlines():
            if needle in line.casefold():
                return line.strip()[:240]
        return text.strip()[:240]
