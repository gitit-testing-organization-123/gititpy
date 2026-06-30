import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


QCC_TAG_SUFFIXES = {".c", ".h"}


@dataclass(frozen=True)
class QccTagsResult:
    generated: bool = False
    skipped: bool = False
    warning: str | None = None


def is_qcc_tag_source(path: Path) -> bool:
    return path.suffix.lower() in QCC_TAG_SUFFIXES


def is_qcc_tags_file(path: Path) -> bool:
    return path.suffix.lower() == ".tags"


def qcc_tags_path(path: Path) -> Path:
    return Path(f"{path}.tags")


def generate_qcc_tags(
    path: Path,
    source_root: Path,
    qcc_command: str = "qcc",
    timeout: int = 30,
) -> QccTagsResult:
    if not is_qcc_tag_source(path):
        return QccTagsResult(skipped=True)

    tag_path = qcc_tags_path(path)
    if tag_path.exists() and tag_path.stat().st_mtime_ns >= path.stat().st_mtime_ns:
        return QccTagsResult(skipped=True)

    qcc = shutil.which(qcc_command)
    if qcc is None:
        return QccTagsResult(warning=f"qcc command not found: {qcc_command}")

    source_root = source_root.resolve()
    path = path.resolve()
    qcc_input, cwd = qcc_input_path(path, source_root)
    env = qcc_environment(source_root)
    try:
        result = subprocess.run(
            [qcc, "-tags", qcc_input],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return QccTagsResult(warning=f"qcc -tags failed for {path}: {exc}")

    if result.returncode != 0:
        detail = first_output_line(result.stderr) or first_output_line(result.stdout)
        suffix = f": {detail}" if detail else ""
        return QccTagsResult(warning=f"qcc -tags failed for {path}{suffix}")
    return QccTagsResult(generated=True)


def qcc_input_path(path: Path, source_root: Path) -> tuple[str, Path]:
    try:
        return path.relative_to(source_root).as_posix(), source_root
    except ValueError:
        return path.name, path.parent


def qcc_environment(source_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    source_root_text = str(source_root)
    env["BASILISK"] = source_root_text
    existing_include_path = env.get("BASILISK_INCLUDE_PATH")
    env["BASILISK_INCLUDE_PATH"] = (
        f"{source_root_text}:{existing_include_path}" if existing_include_path else source_root_text
    )
    return env


def first_output_line(output: str | None) -> str:
    if not output:
        return ""
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return ""
