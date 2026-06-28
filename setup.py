from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py


HELPER_NAMES = ("pagemagic", "literate-c", "codeblock", "sanitize", "urldecode")


class BuildPyWithDarcsitHelpers(build_py):
    def run(self):
        super().run()

        package_src = Path(__file__).parent / "wiki" / "darcsit_helpers" / "src"
        package_dst = Path(self.build_lib) / "wiki" / "darcsit_helpers" / "bin"
        package_dst.mkdir(parents=True, exist_ok=True)

        import os
        import shutil
        import stat
        import subprocess

        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        if not compiler:
            raise RuntimeError("No C compiler found. Set CC or install cc/gcc.")

        for name in HELPER_NAMES:
            source = package_src / f"{name}.c"
            target = package_dst / name
            flags = ["-DYY_NO_UNPUT"]
            if name == "sanitize":
                flags.append("-DYY_NO_INPUT")
            subprocess.run([compiler, *flags, str(source), "-o", str(target)], check=True)
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False


setup(cmdclass={"build_py": BuildPyWithDarcsitHelpers, "bdist_wheel": PlatformWheel})
