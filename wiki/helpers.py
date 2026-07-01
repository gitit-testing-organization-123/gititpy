import hashlib
import os
from pathlib import Path, PurePosixPath


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def title_for(slug: str) -> str:
    return slug.rsplit("/", 1)[-1].replace("_", " ")


def source_parent(slug: str) -> str:
    if not slug or "/" not in slug:
        return ""
    return slug.rsplit("/", 1)[0]


def artifact_rel_dir_for_slug(slug: str) -> str:
    return PurePosixPath(slug).with_suffix("").as_posix()


def artifact_key_dir_for_slug(slug: str, prefix: str = "") -> str:
    rel = artifact_rel_dir_for_slug(slug)
    if not prefix:
        return rel
    return (PurePosixPath(prefix.strip("/")) / rel).as_posix()


def prefixed_source_artifact_path(prefix: str, rel: str) -> str | None:
    normalized = normalized_relative_url_path(rel)
    if normalized is None:
        return None
    return (PurePosixPath(prefix.strip("/")) / normalized).as_posix()


def unique_urls(urls) -> list[str]:
    unique = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def normalized_relative_url_path(path: str) -> str | None:
    normalized = PurePosixPath(os.path.normpath(path.strip("/")))
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() == ".":
        return None
    return normalized.as_posix()


def normalized_source_href_path(path: str) -> str | None:
    normalized = PurePosixPath(os.path.normpath(path))
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() == ".":
        return None
    return normalized.as_posix()


def directory_parent(slug: str) -> str:
    if not slug or "/" not in slug:
        return ""
    return f"{slug.rsplit('/', 1)[0]}/"
