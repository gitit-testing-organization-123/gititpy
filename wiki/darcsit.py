import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


WIKI_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")

LANGUAGES_BY_SUFFIX = {
    ".awk": "awk",
    ".c": "c",
    ".css": "css",
    ".h": "c",
    ".html": "html",
    ".js": "javascript",
    ".m": "matlab",
    ".plot": "bash",
    ".py": "python",
    ".sh": "bash",
}


def render(source: str, slug: str = "", source_path=None) -> str:
    helper_html = render_with_helpers(source, slug, source_path=source_path)
    if helper_html is not None:
        return helper_html
    markup = source_to_markdown(source, slug)
    return render_markdown(markup)


def render_with_helpers(source: str, slug: str, source_path=None) -> str | None:
    language = language_for_slug(slug)
    if not language:
        return None

    if source_path is not None:
        return render_file_with_helpers(str(source_path), slug, language)

    suffix = PurePosixPath(slug).suffix or ".md"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8") as tmp:
        tmp.write(source)
        tmp.flush()
        return render_file_with_helpers(tmp.name, slug, language)


def render_file_with_helpers(path: str, slug: str, language: str) -> str | None:
    helper = DarcsitHelpers()
    if not helper.available():
        raise RuntimeError("Packaged Darcsit helper binaries are not available.")

    if helper.pagemagic(path):
        markdown_source = helper.literate(path)
        if markdown_source is None:
            raise RuntimeError(f"Darcsit literate-c failed for {path}.")
        markdown_source = markdown_source.replace("~~~literatec", "~~~c")
        html_source = render_markdown(markdown_source)
        rendered = helper.codeblock(html_source, path)
        if rendered is None:
            raise RuntimeError(f"Darcsit codeblock failed for {path}.")
        return rendered

    return render_markdown(fenced_code(read_text(path), language))


def source_to_markdown(source: str, slug: str = "") -> str:
    language = language_for_slug(slug)
    if not language:
        return preprocess_wiki_links(source)
    return fenced_code(source, language)


def render_markdown(source: str) -> str:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("Pandoc is required to render wiki pages.")
    try:
        result = subprocess.run(
            [
                pandoc,
                "--from=markdown+smart",
                "--to=html5",
                "--mathjax",
                "--preserve-tabs",
                "--highlight-style=pygments",
            ],
            input=source,
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Pandoc failed: {exc.stderr}") from exc
    return result.stdout


class DarcsitHelpers:
    def __init__(self):
        self.root = self.find_helper_root()
        self.pagemagic_path = self.root / "pagemagic" if self.root else None
        self.literate_path = self.root / "literate-c" if self.root else None
        self.codeblock_path = self.root / "codeblock" if self.root else None

    def find_helper_root(self) -> Path | None:
        packaged_root = Path(__file__).resolve().parent / "darcsit_helpers" / "bin"
        if self.root_has_helpers(packaged_root):
            return packaged_root
        return None

    def root_has_helpers(self, root: Path) -> bool:
        required = ("pagemagic", "literate-c", "codeblock")
        return all(self.is_executable(root / name) for name in required)

    def is_executable(self, path: Path) -> bool:
        return path.is_file() and path.stat().st_mode & 0o111

    def available(self) -> bool:
        return self.root is not None

    def pagemagic(self, path: str) -> bool:
        result = self.run([str(self.pagemagic_path), path], check=False)
        return result is not None and result.returncode == 0

    def literate(self, path: str) -> str | None:
        result = self.run([str(self.literate_path), path, "1"])
        return result.stdout if result else None

    def codeblock(self, html_source: str, path: str) -> str | None:
        result = self.run([str(self.codeblock_path), "", path, "1"], input=html_source)
        return result.stdout if result else None

    def run(
        self,
        args: list[str],
        input: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                args,
                input=input,
                capture_output=True,
                check=check,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def preprocess_wiki_links(source: str) -> str:
    return WIKI_LINK_RE.sub(wiki_link, source)


def wiki_link(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    slug = re.sub(r"\s+", "_", label)
    return f"[{label}]({html.escape(slug, quote=True)})"


def language_for_slug(slug: str) -> str | None:
    name = PurePosixPath(slug).name
    if name in {"Makefile", "makefile"}:
        return "makefile"
    return LANGUAGES_BY_SUFFIX.get(PurePosixPath(slug).suffix.lower())


def fenced_code(source: str, language: str) -> str:
    return f"~~~{language}\n{source.rstrip()}\n~~~"
