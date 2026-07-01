import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .bibliography import render_bibliography_html, replace_bibliography_blocks


WIKI_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
METADATA_BLOCK_RE = re.compile(r"\A---[ \t]*\n(?P<body>.*?)(?:\n\.\.\.[ \t]*\n|\n---[ \t]*\n)", re.DOTALL)
TOC_TEMPLATE = """$if(toc)$<div id="TOC">
$toc$
</div>
$endif$
$body$"""

LANGUAGES_BY_SUFFIX = {
    ".awk": "awk",
    ".c": "c",
    ".cmake": "cmake",
    ".css": "css",
    ".h": "c",
    ".html": "html",
    ".i": "c",
    ".js": "javascript",
    ".json": "json",
    ".m": "matlab",
    ".plot": "bash",
    ".py": "python",
    ".sh": "bash",
}
LITERATE_CODE_LANGUAGES = {"c"}


def render(
    source: str,
    slug: str = "",
    source_path=None,
    table_of_contents: bool = True,
    basilisk_root: Path | None = None,
    basilisk_url: str | None = None,
) -> str:
    if PurePosixPath(slug).suffix.lower() == ".bib":
        return render_bibliography_html(source)
    helper_html = render_with_helpers(
        source,
        slug,
        source_path=source_path,
        table_of_contents=table_of_contents,
        basilisk_root=basilisk_root,
        basilisk_url=basilisk_url,
    )
    if helper_html is not None:
        return helper_html
    return render_page_with_helpers(
        source,
        slug,
        source_path=source_path,
        table_of_contents=table_of_contents,
        basilisk_root=basilisk_root,
        basilisk_url=basilisk_url,
    )


def render_with_helpers(
    source: str,
    slug: str,
    source_path=None,
    table_of_contents: bool = True,
    basilisk_root: Path | None = None,
    basilisk_url: str | None = None,
) -> str | None:
    language = language_for_source(source, slug, source_path=source_path)
    if not language:
        return None

    if source_path is not None:
        return render_file_with_helpers(
            str(source_path),
            slug,
            language,
            table_of_contents=table_of_contents,
            basilisk_root=basilisk_root,
            basilisk_url=basilisk_url,
        )

    suffix = PurePosixPath(slug).suffix or ".md"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8") as tmp:
        tmp.write(source)
        tmp.flush()
        return render_file_with_helpers(
            tmp.name,
            slug,
            language,
            table_of_contents=table_of_contents,
            basilisk_root=basilisk_root,
            basilisk_url=basilisk_url,
        )


def render_file_with_helpers(
    path: str,
    slug: str,
    language: str,
    table_of_contents: bool = True,
    basilisk_root: Path | None = None,
    basilisk_url: str | None = None,
) -> str | None:
    if language not in LITERATE_CODE_LANGUAGES:
        return render_markdown(fenced_code(read_text(path), language), table_of_contents=False)

    helper = DarcsitHelpers(env=darcsit_environment(basilisk_root, basilisk_url))
    if not helper.available():
        raise RuntimeError("Packaged Darcsit helper binaries are not available.")

    if helper.pagemagic(path):
        transformed, replaced = replace_bibliography_blocks(read_text(path))
        if replaced:
            suffix = Path(path).suffix
            with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8") as tmp:
                tmp.write(transformed)
                tmp.flush()
                return render_literate_file(helper, tmp.name, page_magic=True, table_of_contents=table_of_contents)
        return render_literate_file(helper, path, page_magic=True, table_of_contents=table_of_contents)

    return render_markdown(fenced_code(read_text(path), language), table_of_contents=False)


def render_page_with_helpers(
    source: str,
    slug: str,
    source_path=None,
    table_of_contents: bool = True,
    basilisk_root: Path | None = None,
    basilisk_url: str | None = None,
) -> str:
    helper = DarcsitHelpers(env=darcsit_environment(basilisk_root, basilisk_url))
    if not helper.available():
        raise RuntimeError("Packaged Darcsit helper binaries are not available.")

    transformed, _replaced = replace_bibliography_blocks(preprocess_wiki_links(source))
    if source_path is not None and transformed == source:
        return render_literate_file(
            helper,
            str(source_path),
            page_magic=False,
            codeblock_ext=None,
            table_of_contents=table_of_contents,
        )

    suffix = PurePosixPath(slug).suffix or ".md"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8") as tmp:
        tmp.write(transformed)
        tmp.flush()
        return render_literate_file(
            helper,
            tmp.name,
            page_magic=False,
            codeblock_ext=None,
            table_of_contents=table_of_contents,
        )


def render_literate_file(
    helper: "DarcsitHelpers",
    path: str,
    page_magic: bool,
    codeblock_ext: str | None = "1",
    table_of_contents: bool = True,
) -> str:
    markdown_source = helper.literate(path, page_magic=page_magic)
    if markdown_source is None:
        raise RuntimeError(f"Darcsit literate-c failed for {path}.")
    markdown_source = markdown_source.replace("~~~literatec", "~~~c")
    html_source = render_markdown(markdown_source, table_of_contents=table_of_contents)
    rendered = helper.codeblock(html_source, path, ext=codeblock_ext)
    if rendered is None:
        raise RuntimeError(f"Darcsit codeblock failed for {path}.")
    return rendered


def render_markdown(source: str, table_of_contents: bool = True) -> str:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("Pandoc is required to render wiki pages.")
    use_toc = page_table_of_contents(source, table_of_contents)
    args = [
        pandoc,
        "--from=markdown+smart",
        "--to=html5",
        "--mathjax",
        "--preserve-tabs",
        "--highlight-style=pygments",
    ]
    template_file = None
    if use_toc:
        template_file = tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False)
        template_file.write(TOC_TEMPLATE)
        template_file.close()
        args.extend(["--standalone", "--toc", f"--template={template_file.name}"])
    try:
        result = subprocess.run(
            args,
            input=source,
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Pandoc failed: {exc.stderr}") from exc
    finally:
        if template_file is not None:
            Path(template_file.name).unlink(missing_ok=True)
    return result.stdout


def page_table_of_contents(source: str, default: bool) -> bool:
    metadata = parse_metadata_block(source)
    value = metadata.get("toc")
    if value is None:
        return default
    return value.casefold() in {"yes", "true", "1", "on"}


def parse_metadata_block(source: str) -> dict[str, str]:
    match = METADATA_BLOCK_RE.match(source)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    current_key = None
    for line in match.group("body").splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and current_key:
            metadata[current_key] = f"{metadata[current_key]} {line.strip()}"
            continue
        if ":" not in line:
            current_key = None
            continue
        key, value = line.split(":", 1)
        current_key = key.strip().casefold()
        metadata[current_key] = value.strip()
    return metadata


class DarcsitHelpers:
    def __init__(self, env: dict[str, str] | None = None):
        self.env = env
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

    def literate(self, path: str, page_magic: bool = True) -> str | None:
        result = self.run([str(self.literate_path), path, "1" if page_magic else "0"])
        return result.stdout if result else None

    def codeblock(self, html_source: str, path: str, ext: str | None = "1") -> str | None:
        args = [str(self.codeblock_path), "", path]
        if ext is not None:
            args.append(ext)
        result = self.run(args, input=html_source)
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
                env=self.env,
                capture_output=True,
                check=check,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None


def darcsit_environment(basilisk_root: Path | None, basilisk_url: str | None = None) -> dict[str, str] | None:
    if basilisk_root is None and not basilisk_url:
        return None
    env = os.environ.copy()
    if basilisk_root is not None:
        env["BASILISK"] = str(basilisk_root)
    if basilisk_url:
        env["HTTP_BASILISK_URL"] = basilisk_url.rstrip("/")
    return env


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
    lower_name = name.lower()
    if lower_name == "cmakelists.txt":
        return "cmake"
    if lower_name in {"gnumakefile", "makefile"} or lower_name.startswith("makefile."):
        return "makefile"
    return LANGUAGES_BY_SUFFIX.get(PurePosixPath(slug).suffix.lower())


def language_for_source(source: str, slug: str = "", source_path=None) -> str | None:
    language = language_for_slug(slug)
    if language is not None:
        return language
    first_line = source.splitlines()[0] if source else ""
    return language_for_shebang(first_line)


def language_for_shebang(first_line: str) -> str | None:
    if not first_line.startswith("#!"):
        return None
    command = PurePosixPath(first_line.split()[0]).name.lower()
    if command == "env" and len(first_line.split()) > 1:
        command = PurePosixPath(first_line.split()[1]).name.lower()
    if command in {"bash", "sh", "dash", "ksh", "zsh"}:
        return "bash"
    if command in {"python", "python2", "python3"}:
        return "python"
    if command in {"awk", "gawk", "mawk"}:
        return "awk"
    return None


def fenced_code(source: str, language: str) -> str:
    return f"~~~{language}\n{source.rstrip()}\n~~~"
