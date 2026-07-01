from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .source import SourceTree


@dataclass(frozen=True)
class StaticBuildResult:
    output_dir: Path
    html_files: int
    copied_files: int
    skipped_files: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StaticTree:
    name: str
    root: Path
    url_prefix: str
    output_prefix: str
    label: str
    nav_label: str
    kind: Literal["wiki", "source"]
    manifest_page_prefix: str
    manifest_page_kind: str
    search_indexed: bool = True
    generates_tags: bool = False
    artifact_prefix: str | None = None
    artifact_roots: tuple[Path, ...] = ()
    source_tree: SourceTree | None = None

    @property
    def prefix(self) -> str:
        return self.url_prefix

    @property
    def tree(self) -> SourceTree:
        if self.source_tree is None:
            raise AttributeError(f"{self.name} is not backed by a SourceTree")
        return self.source_tree


@dataclass(frozen=True)
class StaticTreeJob:
    action: Literal["render-page", "render-directory", "copy-asset"]
    tree: StaticTree
    rel_path: str
    source_path: Path | None = None
