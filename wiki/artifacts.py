import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .plots import expected_plot_artifacts_for_source


REFERENCE_RE = re.compile(
    r"""(?P<path>/artifacts/[A-Za-z0-9_./~%+-]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_./~%+-]+)"""
)
SOURCE_SUFFIXES = {".c", ".md"}


@dataclass(frozen=True)
class ArtifactRoot:
    name: str
    root: Path
    artifact_root: Path | None = None
    publish_prefix: str = ""


@dataclass(frozen=True)
class ArtifactJob:
    root_name: str
    source_path: Path
    source_rel: str
    artifact_dir: Path
    artifact_rel_dir: str
    artifact_key_dir: str
    existing_artifacts: tuple[str, ...] = ()
    referenced_artifacts: tuple[str, ...] = ()
    derived_artifacts: tuple[str, ...] = ()

    @property
    def has_artifacts(self) -> bool:
        return bool(self.existing_artifacts or self.referenced_artifacts or self.derived_artifacts)

    def to_dict(self) -> dict:
        return {
            "root": self.root_name,
            "source": self.source_rel,
            "artifact_dir": str(self.artifact_dir),
            "artifact_rel_dir": self.artifact_rel_dir,
            "artifact_key_dir": self.artifact_key_dir,
            "existing_artifacts": list(self.existing_artifacts),
            "referenced_artifacts": list(self.referenced_artifacts),
            "derived_artifacts": list(self.derived_artifacts),
        }


def discover_artifact_jobs(roots: list[ArtifactRoot], include_empty: bool = False) -> list[ArtifactJob]:
    jobs = []
    for root in roots:
        if not root.root.exists():
            continue
        for source_path in sorted(root.root.rglob("*")):
            if source_path.suffix not in SOURCE_SUFFIXES:
                continue
            if not is_visible_path(root.root, source_path):
                continue
            job = artifact_job_for_source(root, source_path)
            if include_empty or job.has_artifacts:
                jobs.append(job)
    return jobs


def artifact_job_for_source(root: ArtifactRoot, source_path: Path) -> ArtifactJob:
    source_rel = source_path.relative_to(root.root).as_posix()
    artifact_rel_dir = PurePosixPath(source_rel).with_suffix("").as_posix()
    artifact_dir = (root.artifact_root or root.root) / artifact_rel_dir
    artifact_key_dir = prefixed_artifact_key(root.publish_prefix, artifact_rel_dir)
    derived_artifacts = expected_plot_artifacts_for_source(source_path) if artifact_dir.is_dir() else ()
    return ArtifactJob(
        root_name=root.name,
        source_path=source_path,
        source_rel=source_rel,
        artifact_dir=artifact_dir,
        artifact_rel_dir=artifact_rel_dir,
        artifact_key_dir=artifact_key_dir,
        existing_artifacts=existing_artifacts(artifact_dir),
        referenced_artifacts=referenced_artifacts(source_path, artifact_rel_dir),
        derived_artifacts=derived_artifacts,
    )


def existing_artifacts(artifact_dir: Path) -> tuple[str, ...]:
    if not artifact_dir.is_dir():
        return ()
    paths = []
    for path in sorted(artifact_dir.rglob("*")):
        if is_stageable_artifact(artifact_dir, path):
            paths.append(path.relative_to(artifact_dir).as_posix())
    return tuple(paths)


def is_stageable_artifact(artifact_dir: Path, path: Path) -> bool:
    if not is_visible_path(artifact_dir, path):
        return False
    if path.is_symlink():
        if not path.exists() or not path.resolve().is_file():
            return False
    elif not path.is_file():
        return False
    rel = path.relative_to(artifact_dir)
    # CTest/Basilisk output directories contain the compiled executable
    # as {name}/{name}
    if rel.parts == (artifact_dir.name,):
        return False
    if rel.name == "dump":
        return False
    return True


def referenced_artifacts(source_path: Path, artifact_rel_dir: str) -> tuple[str, ...]:
    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ()

    source_stem = PurePosixPath(artifact_rel_dir).name
    references = set()
    for match in REFERENCE_RE.finditer(source):
        raw_path = strip_trailing_punctuation(match.group("path"))
        normalized = artifact_reference_for_path(raw_path, artifact_rel_dir, source_stem)
        if normalized is not None:
            references.add(normalized)
    return tuple(sorted(references, key=str.casefold))


def artifact_reference_for_path(path: str, artifact_rel_dir: str, source_stem: str) -> str | None:
    if path.endswith("/"):
        return None
    if path.startswith("/artifacts/"):
        rel = path.removeprefix("/artifacts/").strip("/")
        if rel == artifact_rel_dir or rel.startswith(f"{artifact_rel_dir}/"):
            artifact = PurePosixPath(rel).relative_to(PurePosixPath(artifact_rel_dir)).as_posix()
            return None if artifact == "." else artifact
        return None

    rel_path = PurePosixPath(path.strip("/"))
    if not rel_path.parts or rel_path.parts[0] != source_stem:
        return None
    return PurePosixPath(*rel_path.parts[1:]).as_posix() if len(rel_path.parts) > 1 else None


def strip_trailing_punctuation(path: str) -> str:
    return path.rstrip(".,;:")


def is_visible_path(root: Path, path: Path) -> bool:
    rel = path.relative_to(root)
    return ".git" not in rel.parts and not any(part.startswith(".") for part in rel.parts)


def artifact_roots_from_config(config, include_source: bool = True, include_sandbox: bool = True) -> list[ArtifactRoot]:
    roots = []
    source_root = config.resolved_source_root() if include_source else None
    if source_root is not None:
        roots.append(ArtifactRoot("source", source_root, config.resolved_artifact_root(), "src"))
    sandbox_root = config.resolved_sandbox_root() if include_sandbox else None
    if sandbox_root is not None:
        roots.append(ArtifactRoot("sandbox", sandbox_root, config.resolved_sandbox_artifact_root(), "sandbox"))
    return roots


def prefixed_artifact_key(prefix: str, artifact_rel_dir: str) -> str:
    if not prefix:
        return artifact_rel_dir
    return (PurePosixPath(prefix.strip("/")) / artifact_rel_dir).as_posix()


def materialized_artifacts(job: ArtifactJob) -> tuple[str, ...]:
    return tuple(sorted(job.existing_artifacts, key=str.casefold))


def stage_artifacts(jobs: list[ArtifactJob], destination: Path) -> int:
    copied = 0
    for job in jobs:
        for artifact in materialized_artifacts(job):
            source = job.artifact_dir / artifact
            target = destination / job.artifact_key_dir / artifact
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    return copied


def stage_artifact_tree(artifact_root: Path, destination: Path, publish_prefix: str = "") -> int:
    copied = 0
    for artifact_dir in build_artifact_dirs(artifact_root):
        artifact_rel_dir = artifact_dir.relative_to(artifact_root).as_posix()
        artifact_key_dir = prefixed_artifact_key(publish_prefix, artifact_rel_dir)
        for artifact in existing_artifacts(artifact_dir):
            source = artifact_dir / artifact
            target = destination / artifact_key_dir / artifact
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    return copied


def build_artifact_dirs(artifact_root: Path) -> tuple[Path, ...]:
    if not artifact_root.is_dir():
        return ()
    dirs = []
    for path in sorted(artifact_root.rglob("*")):
        if not path.is_dir() or not is_visible_path(artifact_root, path):
            continue
        executable = path / path.name
        if executable.is_file() or executable.is_symlink():
            dirs.append(path)
    return tuple(dirs)


def artifact_manifest(jobs: list[ArtifactJob]) -> list[dict]:
    items = []
    for job in jobs:
        for artifact in materialized_artifacts(job):
            path = job.artifact_dir / artifact
            stat_result = path.stat()
            items.append(
                {
                    "path": f"{job.artifact_key_dir}/{artifact}",
                    "source": str(path),
                    "size": stat_result.st_size,
                    "sha256": file_sha256(path),
                }
            )
    return sorted(items, key=lambda item: item["path"].casefold())


def jobs_to_json(jobs: list[ArtifactJob]) -> str:
    return json.dumps([job.to_dict() for job in jobs], indent=2, sort_keys=True)


def manifest_to_json(manifest: list[dict]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True)


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
