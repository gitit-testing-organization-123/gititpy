from dataclasses import dataclass, replace
from pathlib import Path
import tomllib


DEFAULT_MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"


@dataclass(frozen=True)
class SiteConfig:
    base_dir: Path
    wiki_title: str = "GititPy"
    wiki_root: Path | None = None
    sandbox_root: Path | None = None
    source_root: Path | None = None
    output_dir: Path | None = None
    base_url: str = ""
    artifact_base_url: str = ""
    artifact_root: Path | None = None
    sandbox_artifact_root: Path | None = None
    build_source: bool = True
    generate_source_tags: bool = True
    qcc_command: str = "qcc"
    jobs: int | None = None
    verbose: bool = False
    table_of_contents: bool = True
    mathjax_url: str = DEFAULT_MATHJAX_URL
    template_roots: tuple[Path, ...] = ()
    static_roots: tuple[Path, ...] = ()

    def resolved_wiki_root(self) -> Path:
        return self.resolve_path(self.wiki_root or Path("wiki-pages"))

    def resolved_sandbox_root(self) -> Path | None:
        if self.sandbox_root is not None:
            return self.resolve_path(self.sandbox_root)
        default_sandbox_root = self.base_dir / "sandbox"
        return default_sandbox_root if default_sandbox_root.exists() else None

    def resolved_source_root(self) -> Path | None:
        if not self.build_source:
            return None
        if self.source_root is not None:
            return self.resolve_path(self.source_root)
        default_source_root = self.base_dir / "basilisk" / "src"
        return default_source_root if default_source_root.exists() else None

    def resolved_output_dir(self) -> Path:
        return self.resolve_path(self.output_dir or Path("public"))

    def resolved_artifact_root(self) -> Path | None:
        if self.artifact_root is None:
            return None
        return self.resolve_path(self.artifact_root)

    def resolved_sandbox_artifact_root(self) -> Path | None:
        if self.sandbox_artifact_root is None:
            return None
        return self.resolve_path(self.sandbox_artifact_root)

    def resolved_template_roots(self) -> tuple[Path, ...]:
        if self.template_roots:
            return tuple(self.resolve_path(path) for path in self.template_roots)
        default_template_root = self.base_dir / "templates"
        return (default_template_root,) if default_template_root.exists() else ()

    def resolved_static_roots(self) -> tuple[Path, ...]:
        if self.static_roots:
            return tuple(self.resolve_path(path) for path in self.static_roots)
        default_static_root = self.base_dir / "static"
        return (default_static_root,) if default_static_root.exists() else ()

    def resolve_path(self, path: str | Path) -> Path:
        path = Path(path)
        if path.is_absolute():
            return path
        return self.base_dir / path


def default_config(base_dir: Path | None = None) -> SiteConfig:
    return SiteConfig(base_dir=Path(base_dir or Path.cwd()))


def load_config(base_dir: Path, config_path: Path | None = None) -> SiteConfig:
    config_file = config_path or base_dir / "gititpy.toml"
    if not config_file.exists():
        return SiteConfig(base_dir=base_dir)

    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    site = data.get("site", {})
    paths = data.get("paths", {})
    build = data.get("build", {})
    artifacts = data.get("artifacts", {})

    return SiteConfig(
        base_dir=base_dir,
        wiki_title=site.get("title", "GititPy"),
        mathjax_url=site.get("mathjax_url", DEFAULT_MATHJAX_URL),
        base_url=site.get("base_url", ""),
        artifact_base_url=artifacts.get("base_url", ""),
        artifact_root=optional_path(artifacts.get("source_local_root") or artifacts.get("local_root") or artifacts.get("root")),
        sandbox_artifact_root=optional_path(artifacts.get("sandbox_local_root")),
        table_of_contents=site.get("table_of_contents", True),
        wiki_root=optional_path(paths.get("wiki_root")),
        sandbox_root=optional_path(paths.get("sandbox_root")),
        source_root=optional_path(paths.get("source_root")),
        output_dir=optional_path(paths.get("output")),
        template_roots=tuple(Path(path) for path in paths.get("template_roots", [])),
        static_roots=tuple(Path(path) for path in paths.get("static_roots", [])),
        build_source=build.get("source", True),
        generate_source_tags=build.get("source_tags", True),
        qcc_command=build.get("qcc_command", "qcc"),
        jobs=build.get("jobs"),
        verbose=build.get("verbose", False),
    )


def optional_path(value) -> Path | None:
    if value is None:
        return None
    return Path(value)


def replace_config(config: SiteConfig, **changes) -> SiteConfig:
    return replace(config, **changes)
