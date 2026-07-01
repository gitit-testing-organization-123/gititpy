from dataclasses import dataclass
from pathlib import Path, PurePosixPath


RENDERED_SOURCE_SUFFIXES = {
    "",
    ".awk",
    ".bib",
    ".c",
    ".css",
    ".h",
    ".i",
    ".js",
    ".json",
    ".m",
    ".md",
    ".plot",
    ".py",
    ".sh",
    ".cadna",
    ".mtrace",
    ".paraver",
    ".trace",
    ".wasm",
}


MAKEFILE_NAMES = {"gnumakefile", "makefile"}


class SourcePathError(ValueError):
    pass


@dataclass(frozen=True)
class SourceEntry:
    name: str
    path: str
    is_dir: bool


class SourceTree:
    def __init__(self, root: Path):
        self.root = Path(root)

    def resolve(self, value: str | None = "") -> Path:
        rel = self.normalize(value)
        path = self.root / rel
        root = self.root.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise SourcePathError("Source path escapes the Basilisk root.") from exc
        return path

    def normalize(self, value: str | None = "") -> str:
        rel = (value or "").strip().strip("/")
        if not rel:
            return ""
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts:
            raise SourcePathError("Source paths cannot be absolute or contain '..'.")
        if any(part in {"", "."} or part.startswith(".") for part in path.parts):
            raise SourcePathError("Source paths cannot contain empty or hidden path parts.")
        return path.as_posix()

    def entries(self, value: str | None = "") -> list[SourceEntry]:
        directory = self.resolve(value)
        if not directory.is_dir():
            raise NotADirectoryError(value)
        entries = []
        for child in directory.iterdir():
            if child.name.startswith(".") or child.suffix.lower() == ".tags":
                continue
            rel = child.relative_to(self.root).as_posix()
            entries.append(SourceEntry(child.name, rel, child.is_dir()))
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.casefold()))

    def should_render(self, path: Path) -> bool:
        if path.suffix.lower() == ".tags":
            return False
        if self.is_makefile(path):
            return True
        suffix = path.suffix.lower()
        return suffix in RENDERED_SOURCE_SUFFIXES

    def is_makefile(self, path: Path) -> bool:
        name = path.name.lower()
        if name in MAKEFILE_NAMES or name.startswith("makefile."):
            return True
        try:
            with path.open(encoding="utf-8") as handle:
                first_line = handle.readline(256).lower()
        except (OSError, UnicodeDecodeError):
            return False
        return "makefile" in first_line and "-*-" in first_line
