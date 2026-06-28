from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SiteConfig:
    base_dir: Path
    wiki_title: str = "GititPy"
    wiki_root: Path | None = None
    source_root: Path | None = None
    build_source: bool = True
    jobs: int | None = None
    mathjax_url: str = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"

    def resolved_wiki_root(self) -> Path:
        return self.wiki_root or self.base_dir / "wiki-pages"

    def resolved_source_root(self) -> Path | None:
        if not self.build_source:
            return None
        if self.source_root is not None:
            return self.source_root
        default_source_root = self.base_dir / "basilisk" / "src"
        return default_source_root if default_source_root.exists() else None


def default_config(base_dir: Path | None = None) -> SiteConfig:
    return SiteConfig(base_dir=Path(base_dir or Path.cwd()))
