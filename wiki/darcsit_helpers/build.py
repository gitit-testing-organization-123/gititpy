import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import HELPER_NAMES


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR / "src"
BIN_DIR = PACKAGE_DIR / "bin"


def compile_all(output_dir: Path | None = None) -> list[Path]:
    output_dir = output_dir or BIN_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise RuntimeError("No C compiler found. Set CC or install cc/gcc.")

    built = []
    for name in HELPER_NAMES:
        source = SRC_DIR / f"{name}.c"
        target = output_dir / name
        flags = ["-DYY_NO_UNPUT"]
        if name == "sanitize":
            flags.append("-DYY_NO_INPUT")
        subprocess.run([compiler, *flags, str(source), "-o", str(target)], check=True)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        built.append(target)
    return built


def main() -> int:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        built = compile_all(output_dir)
    except Exception as exc:
        print(f"failed to build Darcsit helpers: {exc}", file=sys.stderr)
        return 1
    for path in built:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
