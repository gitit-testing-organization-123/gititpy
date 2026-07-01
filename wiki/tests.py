import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from gititpy.cli import main
from gititpy.artifacts_cli import main as artifacts_main
from gititpy.config import SiteConfig
from wiki.artifacts import ArtifactRoot, discover_artifact_jobs
from wiki.bibliography import render_bibliography_html
from wiki.darcsit import DarcsitHelpers, render as render_markdown, source_to_markdown
from wiki.plots import expected_plot_artifacts, gnuplot_script_from_source, python_script_from_source
from wiki.static_site import StaticSiteBuilder
from wiki.storage import PageNameError, WikiRepository
from wiki.tags import QccTagsResult, generate_qcc_tags


class GititPyTests(unittest.TestCase):
    def test_front_page_is_seeded_as_plain_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = WikiRepository(Path(tmpdir) / "pages")
            source = repo.read_page("FrontPage")

        self.assertIn("Welcome to GititPy", source)

    def test_page_can_be_written_without_git_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "pages"
            repo = WikiRepository(root)
            repo.write_page("TestPage", "# Test Page\n\nHello from a test.", "Create TestPage")

            self.assertFalse((root / ".git").exists())
            self.assertIn("Hello from a test.", repo.read_page("TestPage"))

    def test_wiki_folder_lists_child_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "pages"
            repo = WikiRepository(root)
            repo.write_page("sandbox/PageOne", "# Page One\n", "Create PageOne")
            repo.write_page("sandbox/nested/PageTwo", "# Page Two\n", "Create PageTwo")
            entries = repo.list_directory("sandbox")

        self.assertEqual([entry.name for entry in entries], ["nested", "PageOne"])
        self.assertTrue(entries[0].is_dir)
        self.assertEqual(entries[0].slug, "sandbox/nested/")

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PageNameError):
            WikiRepository(Path("/tmp/pages")).normalize_slug("../secret")

    def test_markdown_rendering_prefers_pandoc(self):
        completed = mock.Mock(stdout="<h1>Pandoc rendered</h1>\n")
        with mock.patch("wiki.darcsit.shutil.which", return_value="/nix/store/bin/pandoc"):
            with mock.patch("wiki.darcsit.subprocess.run", return_value=completed) as run:
                rendered = render_markdown("# Ignored")

        self.assertEqual(rendered, "<h1>Pandoc rendered</h1>\n")
        pandoc_calls = [
            call_args for call_args in run.call_args_list if call_args.args[0][0] == "/nix/store/bin/pandoc"
        ]
        self.assertEqual(len(pandoc_calls), 1)
        args = pandoc_calls[0].args[0]
        self.assertIn("--mathjax", args)
        self.assertIn("--toc", args)
        self.assertNotIn("--katex", args)

    def test_markdown_rendering_can_disable_toc_with_metadata(self):
        completed = mock.Mock(stdout="<h1>No TOC</h1>\n")
        source = """---
toc: no
...

# No TOC
"""
        with mock.patch("wiki.darcsit.shutil.which", return_value="/nix/store/bin/pandoc"):
            with mock.patch("wiki.darcsit.subprocess.run", return_value=completed) as run:
                render_markdown(source)

        args = run.call_args.args[0]
        self.assertNotIn("--toc", args)

    def test_markdown_rendering_requires_pandoc(self):
        with mock.patch("wiki.darcsit.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Pandoc is required"):
                render_markdown("# Pandoc required")

    def test_source_rendering_requires_packaged_darcsit_helpers(self):
        with mock.patch("wiki.darcsit.DarcsitHelpers.available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Packaged Darcsit helper"):
                render_markdown("int main(void) { return 0; }\n", "missing.c")

    def test_source_page_uses_exact_extension_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "pages"
            repo = WikiRepository(root)
            repo.write_page("examples/hello.c", "int main(void) { return 0; }\n", "Create C page")

            self.assertTrue((root / "examples" / "hello.c").exists())
            self.assertFalse((root / "examples" / "hello.c.md").exists())
            self.assertIn("examples/hello.c", repo.list_pages())

    def test_existing_page_file_with_spaces_and_page_suffix_round_trips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "pages"
            root.mkdir()
            path = root / "Gitit User’s Guide.page"
            path.write_text("# User Guide\n", encoding="utf-8")
            repo = WikiRepository(root)

            slug = repo.page_slug_for_path(path.relative_to(root))

            self.assertEqual(slug, "Gitit User’s Guide")
            self.assertEqual(repo.read_page(slug), "# User Guide\n")
            self.assertEqual(repo.page_path(slug), path)

    def test_source_page_without_magic_renders_as_code(self):
        markup = source_to_markdown("print('hello')\n", "script.py")

        self.assertEqual(markup, "~~~python\nprint('hello')\n~~~")

    def test_c_page_renders_through_darcsit_helpers(self):
        source = """/**
# Helper page

Helper prose.
 */
int main(void) {
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "helper.c"
            path.write_text(source, encoding="utf-8")
            rendered = render_markdown(source, "helper.c", source_path=path)

        self.assertIn("Helper prose", rendered)
        self.assertIn("sourceCode", rendered)
        self.assertIn("id=6", rendered)

    def test_markdown_page_renders_table_of_contents(self):
        source = """# Page

## Section One

Text.

## Section Two

More text.
"""
        rendered = render_markdown(source)

        self.assertIn('id="TOC"', rendered)
        self.assertIn("Section One", rendered)
        self.assertIn("section-one", rendered)

    def test_packaged_darcsit_helpers_are_preferred(self):
        helpers = DarcsitHelpers()

        self.assertTrue(helpers.available())
        self.assertEqual(helpers.root.name, "bin")
        self.assertEqual(helpers.root.parent.name, "darcsit_helpers")

    def test_python_bibliography_renderer_formats_bibtex(self):
        rendered = render_bibliography_html(
            """@article{doe2024,
  title = {A Bibliography Test},
  author = {Doe, Jane},
  journal = {Journal of Tests},
  year = {2024}
}
"""
        )

        self.assertIn("bibtex", rendered)
        self.assertIn("Jane Doe", rendered)
        self.assertIn("A Bibliography Test", rendered)

    def test_python_bibliography_renderer_expands_hal_entries(self):
        hal_response = BytesIO(
            b"""@article{serverkey,
  title = {HAL Result},
  author = {Doe, Jane},
  year = {2024}
}
"""
        )
        with mock.patch("wiki.bibliography.urlopen", return_value=hal_response):
            rendered = render_bibliography_html("@hal{localkey, hal-123456}\n")

        self.assertIn('name="localkey"', rendered)
        self.assertIn("HAL Result", rendered)

    def test_artifact_detector_finds_basilisk_sibling_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "src"
            artifact_dir = root / "test" / "vortex"
            artifact_dir.mkdir(parents=True)
            (root / "test" / "vortex.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (artifact_dir / "plot.png").write_bytes(b"png")
            (artifact_dir / "plot.png.tags").write_text("decl x y z\n", encoding="utf-8")

            jobs = discover_artifact_jobs([ArtifactRoot("source", root)])

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].source_rel, "test/vortex.c")
            self.assertEqual(jobs[0].artifact_rel_dir, "test/vortex")
            self.assertEqual(jobs[0].existing_artifacts, ("plot.png", "plot.png.tags"))

    def test_artifact_detector_can_use_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "src"
            artifact_root = Path(tmpdir) / "build" / "src"
            artifact_dir = artifact_root / "test" / "vortex"
            (root / "test").mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (root / "test" / "vortex.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (artifact_dir / "movie.mp4").write_bytes(b"movie")

            jobs = discover_artifact_jobs([ArtifactRoot("source", root, artifact_root)])

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].artifact_dir, artifact_dir)
            self.assertEqual(jobs[0].existing_artifacts, ("movie.mp4",))

    def test_artifact_detector_finds_references_without_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "src"
            (root / "examples").mkdir(parents=True)
            (root / "examples" / "bubble.c").write_text(
                """/**
![Plot](bubble/plot.png)
<video src="/artifacts/examples/bubble/movie.mp4"></video>
[Directory](bubble/1024/)
 */
int main(void) { return 0; }
""",
                encoding="utf-8",
            )

            jobs = discover_artifact_jobs([ArtifactRoot("source", root)])

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].referenced_artifacts, ("movie.mp4", "plot.png"))

    def test_artifact_detector_finds_derived_gnuplot_outputs(self):
        source = """/**
~~~gnuplot Default plot
plot 'out'
~~~

~~~gnuplot PNG plot
set output 'plot.png'
plot 'out'
~~~
 */
int main(void) { return 0; }
"""
        self.assertEqual(expected_plot_artifacts(source), ("_plot0.svg", "plot.png"))

        script = gnuplot_script_from_source(source)

        self.assertIn("set output '_plot0.svg';", script)
        self.assertIn("set output 'plot.png'", script)
        self.assertIn("mogrify -trim plot.png", script)
        self.assertEqual(python_script_from_source(source), "")

    def test_artifacts_cli_lists_detected_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_dir = source_root / "test" / "vortex"
            artifact_dir.mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (artifact_dir / "plot.png").write_bytes(b"png")

            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(["--base-dir", str(root), "list"])

            self.assertEqual(status, 0)
            self.assertIn("source:test/vortex.c", stdout.getvalue())
            self.assertIn("src/test/vortex/plot.png", stdout.getvalue())

    def test_artifacts_cli_lists_derived_plot_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_dir = source_root / "test" / "vortex"
            (source_root / "test").mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text(
                "/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n",
                encoding="utf-8",
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(["--base-dir", str(root), "plots", "list"])

            self.assertEqual(status, 0)
            self.assertIn("source:test/vortex.c", stdout.getvalue())
            self.assertIn("src/test/vortex/plot.png", stdout.getvalue())

    def test_artifacts_cli_skips_derived_plot_artifacts_without_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text(
                "/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n",
                encoding="utf-8",
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(["--base-dir", str(root), "plots", "list"])

            self.assertEqual(status, 0)
            self.assertNotIn("source:test/vortex.c", stdout.getvalue())
            self.assertNotIn("src/test/vortex/plot.png", stdout.getvalue())

    def test_artifacts_cli_stages_from_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_root = root / "basilisk" / "build" / "release" / "src"
            artifact_dir = artifact_root / "test" / "vortex"
            generated_artifact_dir = artifact_root / "test" / "dirichlet"
            stage_dir = root / "stage"
            (source_root / "test").mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            generated_artifact_dir.mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text(
                "/**\n![Plot](vortex/plot.png)\n*/\nint main(void) { return 0; }\n",
                encoding="utf-8",
            )
            (artifact_dir / "plot.png").write_bytes(b"png")
            (artifact_dir / "log").write_text("log\n", encoding="utf-8")
            (artifact_dir / "dump").write_bytes(b"dump")
            (artifact_dir / "vortex").write_bytes(b"binary")
            (artifact_dir / "linked.png").symlink_to(artifact_dir / "plot.png")
            (generated_artifact_dir / "dirichlet").write_bytes(b"binary")
            (generated_artifact_dir / "a.png").write_bytes(b"a-png")
            (generated_artifact_dir / "dump").write_bytes(b"dump")
            (artifact_root / "CMakeFiles").mkdir()
            (artifact_root / "CMakeFiles" / "noise.txt").write_text("noise\n", encoding="utf-8")

            status = artifacts_main(
                [
                    "--base-dir",
                    str(root),
                    "--artifact-root",
                    str(artifact_root),
                    "stage",
                    "--dest",
                    str(stage_dir),
                ]
            )

            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / "src" / "test" / "vortex" / "plot.png").read_bytes(), b"png")
            self.assertEqual((stage_dir / "src" / "test" / "vortex" / "log").read_text(encoding="utf-8"), "log\n")
            self.assertEqual((stage_dir / "src" / "test" / "vortex" / "linked.png").read_bytes(), b"png")
            self.assertEqual((stage_dir / "src" / "test" / "dirichlet" / "a.png").read_bytes(), b"a-png")
            self.assertFalse((stage_dir / "src" / "test" / "vortex" / "dump").exists())
            self.assertFalse((stage_dir / "src" / "test" / "vortex" / "vortex").exists())
            self.assertFalse((stage_dir / "src" / "test" / "dirichlet" / "dump").exists())
            self.assertFalse((stage_dir / "src" / "test" / "dirichlet" / "dirichlet").exists())
            self.assertFalse((stage_dir / "src" / "CMakeFiles" / "noise.txt").exists())

    def test_artifacts_cli_stages_sandbox_from_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_artifact_root = root / "basilisk" / "build" / "release" / "src"
            sandbox_root = root / "sandbox"
            sandbox_artifact_root = root / "sandbox-build"
            source_artifact_dir = source_artifact_root / "test" / "vortex"
            sandbox_artifact_dir = sandbox_artifact_root / "cases" / "drop"
            stage_dir = root / "stage"
            (sandbox_root / "cases").mkdir(parents=True)
            source_artifact_dir.mkdir(parents=True)
            sandbox_artifact_dir.mkdir(parents=True)
            (sandbox_root / "cases" / "drop.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (source_artifact_dir / "vortex").write_bytes(b"binary")
            (source_artifact_dir / "plot.png").write_bytes(b"source-png")
            (sandbox_artifact_dir / "drop").write_bytes(b"binary")
            (sandbox_artifact_dir / "plot.png").write_bytes(b"sandbox-png")

            status = artifacts_main(
                [
                    "--base-dir",
                    str(root),
                    "--artifact-root",
                    str(source_artifact_root),
                    "--sandbox-root",
                    str(sandbox_root),
                    "--sandbox-artifact-root",
                    str(sandbox_artifact_root),
                    "--scope",
                    "sandbox",
                    "stage",
                    "--dest",
                    str(stage_dir),
                ]
            )

            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / "sandbox" / "cases" / "drop" / "plot.png").read_bytes(), b"sandbox-png")
            self.assertFalse((stage_dir / "sandbox" / "cases" / "drop" / "drop").exists())
            self.assertFalse((stage_dir / "src" / "test" / "vortex" / "plot.png").exists())

    def test_artifacts_cli_stages_source_and_sandbox_roots_disjointly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            source_artifact_root = root / "basilisk" / "build" / "release" / "src"
            sandbox_root = root / "sandbox"
            sandbox_artifact_root = root / "sandbox-build"
            source_artifact_dir = source_artifact_root / "test" / "vortex"
            sandbox_artifact_dir = sandbox_artifact_root / "cases" / "drop"
            stage_dir = root / "stage"
            (source_root / "test").mkdir(parents=True)
            (sandbox_root / "cases").mkdir(parents=True)
            source_artifact_dir.mkdir(parents=True)
            sandbox_artifact_dir.mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (sandbox_root / "cases" / "drop.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (source_artifact_dir / "vortex").write_bytes(b"binary")
            (source_artifact_dir / "plot.png").write_bytes(b"source-png")
            (sandbox_artifact_dir / "drop").write_bytes(b"binary")
            (sandbox_artifact_dir / "plot.png").write_bytes(b"sandbox-png")

            status = artifacts_main(
                [
                    "--base-dir",
                    str(root),
                    "--artifact-root",
                    str(source_artifact_root),
                    "--sandbox-root",
                    str(sandbox_root),
                    "--sandbox-artifact-root",
                    str(sandbox_artifact_root),
                    "stage",
                    "--dest",
                    str(stage_dir),
                ]
            )

            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / "src" / "test" / "vortex" / "plot.png").read_bytes(), b"source-png")
            self.assertEqual((stage_dir / "sandbox" / "cases" / "drop" / "plot.png").read_bytes(), b"sandbox-png")

    def test_artifacts_cli_generates_plot_scripts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_root = root / "basilisk" / "build" / "release" / "src"
            artifact_dir = artifact_root / "test" / "vortex"
            (source_root / "test").mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text(
                """/**
~~~gnuplot Plot
set output 'plot.png'
plot 'out'
~~~

~~~pythonplot Py plot
import matplotlib.pyplot as plt
plt.savefig('py.png')
~~~
 */
int main(void) { return 0; }
""",
                encoding="utf-8",
            )
            completed = mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("wiki.plots.subprocess.run", return_value=completed) as run:
                status = artifacts_main(
                    [
                        "--base-dir",
                        str(root),
                        "--artifact-root",
                        str(artifact_root),
                        "plots",
                        "generate",
                        "--gnuplot-command",
                        "gnuplot",
                        "--python-command",
                        "python",
                    ]
                )

            self.assertEqual(status, 0)
            self.assertIn("set output 'plot.png'", (artifact_dir / "plots").read_text(encoding="utf-8"))
            self.assertIn("plt.savefig('py.png')", (artifact_dir / "plots.py").read_text(encoding="utf-8"))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][0], "gnuplot")
            self.assertIn("set term svg enhanced", run.call_args_list[0].args[0][2])
            self.assertNotIn("set term @SVG", run.call_args_list[0].args[0][2])
            self.assertEqual(run.call_args_list[0].kwargs["cwd"], artifact_dir)
            self.assertEqual(run.call_args_list[1].args[0], ["python", "plots.py"])
            self.assertEqual(run.call_args_list[1].kwargs["cwd"], artifact_dir)

    def test_artifacts_cli_links_source_plot_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_root = root / "basilisk" / "build" / "release" / "src"
            artifact_dir = artifact_root / "test" / "vortex"
            source_dir = source_root / "test"
            source_aux_dir = source_dir / "vortex"
            source_dir.mkdir(parents=True)
            source_aux_dir.mkdir()
            artifact_dir.mkdir(parents=True)
            (source_dir / "vortex.c").write_text(
                "/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'vortex.ref', 'profile.dat'\n~~~\n*/\n",
                encoding="utf-8",
            )
            (source_dir / "vortex.ref").write_text("1 2\n", encoding="utf-8")
            (source_aux_dir / "profile.dat").write_text("1 3\n", encoding="utf-8")
            (source_aux_dir / ".hidden").write_text("hidden\n", encoding="utf-8")
            completed = mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("wiki.plots.subprocess.run", return_value=completed):
                status = artifacts_main(
                    [
                        "--base-dir",
                        str(root),
                        "--artifact-root",
                        str(artifact_root),
                        "plots",
                        "generate",
                    ]
                )

            self.assertEqual(status, 0)
            self.assertTrue((artifact_dir / "vortex.ref").is_symlink())
            self.assertEqual((artifact_dir / "vortex.ref").resolve(), (source_dir / "vortex.ref").resolve())
            self.assertTrue((artifact_dir / "profile.dat").is_symlink())
            self.assertEqual((artifact_dir / "profile.dat").resolve(), (source_aux_dir / "profile.dat").resolve())
            self.assertFalse((artifact_dir / ".hidden").exists())

    def test_artifacts_cli_does_not_generate_plots_without_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / "basilisk" / "src"
            artifact_root = root / "basilisk" / "build" / "release" / "src"
            artifact_dir = artifact_root / "test" / "vortex"
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text(
                "/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n",
                encoding="utf-8",
            )

            stdout = StringIO()
            with mock.patch("wiki.plots.subprocess.run") as run, redirect_stdout(stdout):
                status = artifacts_main(
                    [
                        "--base-dir",
                        str(root),
                        "--artifact-root",
                        str(artifact_root),
                        "plots",
                        "generate",
                    ]
                )

            self.assertEqual(status, 0)
            self.assertIn("Generated plot artifacts for 0 source file(s)", stdout.getvalue())
            self.assertFalse(artifact_dir.exists())
            run.assert_not_called()

    def test_c_page_renders_bibliography_block(self):
        source = """/**
# Bibliography page

~~~bib
@article{doe2024,
  title = {A Bibliography Test},
  author = {Doe, Jane},
  journal = {Journal of Tests},
  year = {2024}
}
~~~
 */
int main(void) {
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bib.c"
            path.write_text(source, encoding="utf-8")
            rendered = render_markdown(source, "bib.c", source_path=path)

        self.assertIn("bibtex", rendered)
        self.assertIn("A Bibliography Test", rendered)

    def test_qcc_tags_generation_sets_basilisk_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_root = Path(tmpdir) / "basilisk" / "src"
            source_root.mkdir(parents=True)
            source_path = source_root / "example.c"
            source_path.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            completed = mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("wiki.tags.shutil.which", return_value="/nix/store/bin/qcc"):
                with mock.patch("wiki.tags.subprocess.run", return_value=completed) as run:
                    result = generate_qcc_tags(source_path, source_root)

            self.assertTrue(result.generated)
            self.assertEqual(run.call_args.args[0], ["/nix/store/bin/qcc", "-tags", "example.c"])
            self.assertEqual(run.call_args.kwargs["cwd"], str(source_root.resolve()))
            self.assertEqual(run.call_args.kwargs["env"]["BASILISK"], str(source_root))
            self.assertEqual(run.call_args.kwargs["env"]["BASILISK_INCLUDE_PATH"], str(source_root))

    def test_qcc_tags_generation_uses_source_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ("long-source-root-" + "x" * 80)
            source_root = root / "basilisk" / "src"
            source_path = source_root / "gotm" / "turbulence" / "r_ratio.h"
            source_path.parent.mkdir(parents=True)
            source_path.write_text("static inline void turbulence_r_ratio (void) {}\n", encoding="utf-8")
            completed = mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("wiki.tags.shutil.which", return_value="/nix/store/bin/qcc"):
                with mock.patch("wiki.tags.subprocess.run", return_value=completed) as run:
                    result = generate_qcc_tags(source_path, source_root)

            self.assertTrue(result.generated)
            self.assertEqual(run.call_args.args[0], ["/nix/store/bin/qcc", "-tags", "gotm/turbulence/r_ratio.h"])
            self.assertLess(len(run.call_args.args[0][2] + ".tags"), 80)

    def test_qcc_tags_generation_reports_missing_qcc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_root = Path(tmpdir)
            source_path = source_root / "example.c"
            source_path.write_text("int main(void) { return 0; }\n", encoding="utf-8")

            with mock.patch("wiki.tags.shutil.which", return_value=None):
                result = generate_qcc_tags(source_path, source_root)

            self.assertIn("qcc command not found", result.warning)

    def test_static_build_renders_pages_and_assets_without_source_when_no_source_tree_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            repo = WikiRepository(wiki_root)
            repo.write_page("FrontPage", "# Static Front\n\nSee [[Help]].", "Create front")
            repo.write_page("sandbox", "# Sandbox page\n", "Create sandbox page")
            repo.write_page("sandbox/PageOne", "# Page One\n", "Create PageOne")
            (wiki_root / "sandbox" / "movie.mp4").write_bytes(b"movie")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root),
                output_dir=output,
            )
            builder.build()

            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "sandbox.html").is_file())
            self.assertTrue((output / "sandbox" / "index.html").is_file())
            self.assertTrue((output / "sandbox" / "movie.mp4").is_file())
            self.assertFalse((output / "src" / "index.html").exists())
            self.assertTrue((output / "static" / "wiki" / "css" / "gititpy.css").is_file())
            self.assertTrue((output / "static" / "wiki" / "js" / "search.js").is_file())
            self.assertTrue((output / "search-index.json").is_file())
            self.assertIn("Static Front", (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn('href="/sandbox.html"', (output / "_index.html").read_text(encoding="utf-8"))
            self.assertIn('href="/sandbox/PageOne.html"', (output / "sandbox" / "index.html").read_text(encoding="utf-8"))

    def test_static_build_renders_default_source_tree_in_parallel(self):
        source = """/**
# Source file

Rendered statically.
 */
int main(void) {
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n\n[Source](/src/example.c)\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "example.c").write_text(source, encoding="utf-8")
            (source_root / "README.md").write_text("[Example](example.c)\n", encoding="utf-8")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=2),
                output_dir=output,
            )
            builder.build()

            self.assertTrue((output / "src" / "index.html").is_file())
            self.assertTrue((output / "src" / "example.c" / "index.html").is_file())
            self.assertFalse((output / "src" / "example.c.html").exists())
            self.assertIn("Rendered statically.", (output / "src" / "example.c" / "index.html").read_text(encoding="utf-8"))
            self.assertIn('href="/src/example.c/"', (output / "src" / "index.html").read_text(encoding="utf-8"))
            self.assertIn('href="/src/example.c/"', (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn('href="/src/example.c/"', (output / "src" / "README.md" / "index.html").read_text(encoding="utf-8"))

    def test_static_build_writes_sitemap_and_robots_txt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            sandbox_root = root / "sandbox"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            WikiRepository(wiki_root).write_page("Guide", "# Guide\n", "Create guide")
            WikiRepository(wiki_root).write_page("docs/Page", "# Docs\n", "Create docs")
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page("README", "# Sandbox\n", "Create sandbox")
            (source_root / "sub").mkdir(parents=True)
            (source_root / "sub" / "example.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (source_root / "sub" / "example.c.tags").write_text("decl main sub/example.c 1\n", encoding="utf-8")
            (source_root / "blob.bin").write_bytes(b"\x00\x01")

            StaticSiteBuilder(
                config=SiteConfig(
                    base_dir=root,
                    wiki_root=wiki_root,
                    sandbox_root=sandbox_root,
                    source_root=source_root,
                    generate_source_tags=False,
                    jobs=1,
                ),
                output_dir=output,
                base_url="https://example.org/docs",
            ).build()

            sitemap = (output / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("<loc>https://example.org/docs/</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/Guide.html</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/docs/</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/docs/Page.html</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/sandbox/README.html</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/src/sub/</loc>", sitemap)
            self.assertIn("<loc>https://example.org/docs/src/sub/example.c/</loc>", sitemap)
            self.assertNotIn("example.c.tags", sitemap)
            self.assertNotIn("blob.bin", sitemap)
            self.assertNotIn("_search.html", sitemap)
            self.assertNotIn("_history", sitemap)
            self.assertNotIn("_raw", sitemap)

            robots = (output / "robots.txt").read_text(encoding="utf-8")
            self.assertIn("Allow: /docs/", robots)
            self.assertIn("Disallow: /docs/_raw/", robots)
            self.assertIn("Disallow: /docs/_history/", robots)
            self.assertIn("Disallow: /docs/_search.html", robots)
            self.assertIn("Sitemap: https://example.org/docs/sitemap.xml", robots)

    def test_static_build_writes_canonical_links_for_indexable_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            sandbox_root = root / "sandbox"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            WikiRepository(wiki_root).write_page("Guide", "# Guide\n", "Create guide")
            WikiRepository(wiki_root).write_page("docs/Page", "# Docs\n", "Create docs")
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page("README", "# Sandbox\n", "Create sandbox")
            (source_root / "sub").mkdir(parents=True)
            (source_root / "sub" / "example.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

            StaticSiteBuilder(
                config=SiteConfig(
                    base_dir=root,
                    wiki_root=wiki_root,
                    sandbox_root=sandbox_root,
                    source_root=source_root,
                    generate_source_tags=False,
                    jobs=1,
                ),
                output_dir=output,
                base_url="https://example.org/docs",
            ).build()

            self.assertIn(
                '<link rel="canonical" href="https://example.org/docs/">',
                (output / "index.html").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '<link rel="canonical" href="https://example.org/docs/Guide.html">',
                (output / "Guide.html").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '<link rel="canonical" href="https://example.org/docs/docs/">',
                (output / "docs" / "index.html").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '<link rel="canonical" href="https://example.org/docs/sandbox/README.html">',
                (output / "sandbox" / "README.html").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '<link rel="canonical" href="https://example.org/docs/src/sub/example.c/">',
                (output / "src" / "sub" / "example.c" / "index.html").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                'rel="canonical"',
                (output / "_search.html").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                'rel="canonical"',
                (output / "_history" / "Guide.html").read_text(encoding="utf-8"),
            )

    def test_static_build_can_skip_default_source_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "example.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                output_dir=output,
            )
            builder.build()

            self.assertFalse((output / "src" / "index.html").exists())

    def test_static_build_rewrites_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            rendered_content = (
                '<a href="/artifacts/examples/bubble/movie.mp4">movie</a>'
                '<img src="/artifacts/examples/bubble/plot 1.png?download=1#frame">'
                '<video poster="/artifacts/examples/bubble/poster.png" src="/local.mp4"></video>'
                '<a href="/Help.html">help</a>'
            )

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        build_source=False,
                        artifact_base_url="https://artifacts.example.org/site",
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="https://artifacts.example.org/site/examples/bubble/movie.mp4"', rendered)
            self.assertIn('src="https://artifacts.example.org/site/examples/bubble/plot%201.png?download=1#frame"', rendered)
            self.assertIn('poster="https://artifacts.example.org/site/examples/bubble/poster.png"', rendered)
            self.assertIn('href="/Help.html"', rendered)
            self.assertIn('src="/local.mp4"', rendered)

    def test_static_source_render_rewrites_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "vortex.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            rendered_content = (
                '<a href="vortex/vort.mp4">movie</a>'
                '<img src="vortex/plot 1.png?download=1#frame">'
                '<a href="other/movie.mp4">other</a>'
            )

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="https://artifacts.example.org/site",
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "test" / "vortex.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="https://artifacts.example.org/site/src/test/vortex/vort.mp4"', rendered)
            self.assertIn('src="https://artifacts.example.org/site/src/test/vortex/plot%201.png?download=1#frame"', rendered)
            self.assertIn('href="other/movie.mp4"', rendered)

    def test_static_source_render_rewrites_cross_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            artifact_root = root / "basilisk" / "build" / "release" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "neumann.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (artifact_root / "test" / "dirichlet").mkdir(parents=True)
            (artifact_root / "test" / "dirichlet" / "a.png").write_bytes(b"png")
            rendered_content = (
                '<img src="dirichlet/a.png?1782833204">'
                '<a href="other/movie.mp4">other</a>'
            )

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="/artifacts",
                        artifact_root=artifact_root,
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "test" / "neumann.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="/artifacts/src/test/dirichlet/a.png?1782833204"', rendered)
            self.assertIn('href="other/movie.mp4"', rendered)

    def test_static_sandbox_render_rewrites_cross_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            sandbox_root = root / "sandbox"
            sandbox_artifact_root = root / "sandbox-build"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (sandbox_root / "cases").mkdir(parents=True)
            (sandbox_root / "cases" / "neumann.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (sandbox_artifact_root / "cases" / "dirichlet").mkdir(parents=True)
            (sandbox_artifact_root / "cases" / "dirichlet" / "a.png").write_bytes(b"png")
            rendered_content = '<img src="dirichlet/a.png?1782833204">'

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        sandbox_root=sandbox_root,
                        sandbox_artifact_root=sandbox_artifact_root,
                        artifact_base_url="/artifacts",
                        build_source=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "sandbox" / "cases" / "neumann.c.html").read_text(encoding="utf-8")
            self.assertIn('src="/artifacts/sandbox/cases/dirichlet/a.png?1782833204"', rendered)

    def test_static_source_render_rewrites_absolute_source_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "test").mkdir(parents=True)
            source_path = source_root / "test" / "stokes.c"
            source_path.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            rendered_content = f'<img src="{source_root}/test/stokes/_plot0.svg?1782798642">'

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="/artifacts",
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "test" / "stokes.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="/artifacts/src/test/stokes/_plot0.svg?1782798642"', rendered)
            self.assertNotIn(str(source_root), rendered)

    def test_static_source_render_rewrites_temp_plot_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "stokes.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            rendered_content = '<img src="/tmp/tmpabc123/_plot0.svg?1782798642">'

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="/artifacts",
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "test" / "stokes.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="/artifacts/src/test/stokes/_plot0.svg?1782798642"', rendered)
            self.assertNotIn("/tmp/tmpabc123", rendered)

    def test_static_source_render_rewrites_named_temp_plot_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "test").mkdir(parents=True)
            (source_root / "test" / "static_bubble.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            rendered_content = '<img src="/tmp/tmp3y5ywol9/p001cbt.svg?1782840608">'

            with mock.patch("wiki.static_site.render_darcsit", return_value=rendered_content):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="/artifacts",
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "test" / "static_bubble.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="/artifacts/src/test/static_bubble/p001cbt.svg?1782840608"', rendered)
            self.assertNotIn("/tmp/tmp3y5ywol9", rendered)

    def test_static_build_renders_sandbox_from_separate_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            sandbox_root = root / "sandbox"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            WikiRepository(wiki_root).write_page("sandbox/OldPage", "# Old Sandbox\n", "Create old sandbox")
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page("README", "# New Sandbox\n\nsearchable sandbox text", "Create sandbox")
            sandbox_repo.write_page("user/README", "# User Sandbox\n", "Create user sandbox")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False),
                output_dir=output,
            )
            builder.build()

            self.assertTrue((output / "sandbox" / "index.html").is_file())
            self.assertTrue((output / "sandbox" / "README.html").is_file())
            self.assertTrue((output / "sandbox" / "user" / "index.html").is_file())
            self.assertTrue((output / "sandbox" / "user" / "README.html").is_file())
            self.assertFalse((output / "sandbox" / "OldPage.html").exists())
            self.assertFalse((sandbox_root / "FrontPage.md").exists())
            self.assertFalse((sandbox_root / "Help.md").exists())
            self.assertIn("New Sandbox", (output / "sandbox" / "README.html").read_text(encoding="utf-8"))
            self.assertIn(
                'href="/sandbox/user/"',
                (output / "sandbox" / "index.html").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'href="/sandbox/"',
                (output / "sandbox" / "user" / "index.html").read_text(encoding="utf-8"),
            )
            search_index = (output / "search-index.json").read_text(encoding="utf-8")
            self.assertIn("sandbox/README", search_index)
            self.assertIn("/sandbox/README.html", search_index)

    def test_incremental_build_skips_unchanged_page_renders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Incremental Front\n", "Create front")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                output_dir=output,
            )
            builder.build()

            with mock.patch("wiki.static_site.render_darcsit", side_effect=AssertionError("should skip")):
                result = StaticSiteBuilder(
                    config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                    output_dir=output,
                ).build()

            self.assertGreater(result.skipped_files, 0)
            self.assertTrue((output / ".gititpy-build.json").is_file())

    def test_incremental_build_uses_content_hash_not_mtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            repo = WikiRepository(wiki_root)
            repo.write_page("FrontPage", "# Hash Front\n", "Create front")

            StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                output_dir=output,
            ).build()

            source_path = repo.page_path("FrontPage")
            stat_result = source_path.stat()
            os.utime(source_path, ns=(stat_result.st_atime_ns + 1_000_000_000, stat_result.st_mtime_ns + 1_000_000_000))

            with mock.patch("wiki.static_site.render_darcsit", side_effect=AssertionError("should skip")):
                result = StaticSiteBuilder(
                    config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                    output_dir=output,
                ).build()

            manifest = json.loads((output / ".gititpy-build.json").read_text(encoding="utf-8"))
            item = manifest["items"]["wiki:FrontPage"]
            self.assertGreater(result.skipped_files, 0)
            self.assertIn("sha256", item)
            self.assertNotIn("mtime_ns", item)

    def test_incremental_build_rerenders_changed_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            repo = WikiRepository(wiki_root)
            repo.write_page("FrontPage", "# Incremental Front\n", "Create front")

            StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                output_dir=output,
            ).build()
            repo.write_page("FrontPage", "# Incremental Front Changed\n\nExtra text.\n", "Change front")

            with mock.patch("wiki.static_site.render_darcsit", return_value="<p>Changed render</p>") as render:
                StaticSiteBuilder(
                    config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                    output_dir=output,
                ).build()

            self.assertEqual(render.call_count, 1)
            self.assertIn("Changed render", (output / "index.html").read_text(encoding="utf-8"))

    def test_force_rebuild_ignores_incremental_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Incremental Front\n", "Create front")

            StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                output_dir=output,
            ).build()

            with mock.patch("wiki.static_site.render_darcsit", return_value="<p>Forced render</p>") as render:
                result = StaticSiteBuilder(
                    config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False),
                    output_dir=output,
                ).build(clean=True, force_rebuild=True)

            self.assertGreaterEqual(render.call_count, 1)
            self.assertEqual(result.skipped_files, 0)
            self.assertIn("Forced render", (output / "index.html").read_text(encoding="utf-8"))

    def test_source_render_failure_falls_back_to_plain_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "generated.c").write_text("int generated(void) { return 1; }\n", encoding="utf-8")

            def render_or_fail(source, slug="", source_path=None, table_of_contents=True, basilisk_root=None):
                if slug == "generated.c":
                    raise RuntimeError("Darcsit literate-c failed for generated.c.")
                return "<p>Rendered page</p>"

            with mock.patch("wiki.static_site.render_darcsit", side_effect=render_or_fail):
                builder = StaticSiteBuilder(
                    config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1),
                    output_dir=output,
                )
                result = builder.build()

            rendered = (output / "src" / "generated.c" / "index.html").read_text(encoding="utf-8")
            self.assertIn("generated", rendered)
            self.assertIn("plain code", result.warnings[0])

    def test_static_source_render_generates_qcc_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            source_path = source_root / "example.c"
            source_path.write_text("int main(void) { return 0; }\n", encoding="utf-8")

            with mock.patch("wiki.static_site.render_darcsit", return_value="<p>Rendered</p>"):
                with mock.patch(
                    "wiki.static_site.generate_qcc_tags",
                    return_value=QccTagsResult(generated=True),
                ) as generate:
                    StaticSiteBuilder(
                        config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1),
                        output_dir=output,
                    ).build()

            generate.assert_called_once_with(source_path, source_root, "qcc")

    def test_relative_source_links_rewrite_to_absolute_source_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "all-mach.h").write_text(
                """/**
[Poisson](poisson.h)
 */
""",
                encoding="utf-8",
            )
            (source_root / "poisson.h").write_text("int poisson(void);\n", encoding="utf-8")

            builder = StaticSiteBuilder(
                config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1),
                output_dir=output,
            )
            builder.build()

            rendered = (output / "src" / "all-mach.h" / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="/src/poisson.h/"', rendered)
            self.assertNotIn('href="poisson.h/"', rendered)

    def test_unquoted_relative_source_links_rewrite_to_absolute_source_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "all-mach.h").write_text('#include "run.h"\n', encoding="utf-8")
            (source_root / "run.h").write_text("void run(void);\n", encoding="utf-8")

            with mock.patch(
                "wiki.static_site.render_darcsit",
                return_value='<span class="im">&quot;<a href=./run.h>run.h</a>&quot;</span>',
            ):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "all-mach.h" / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="/src/run.h/"', rendered)
            self.assertNotIn("href=./run.h", rendered)

    def test_source_root_relative_tag_links_are_not_rewritten_as_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            (source_root / "ast").mkdir(parents=True)
            (source_root / "ast" / "ast.h").write_text('#include "stack.h"\n', encoding="utf-8")
            (source_root / "ast" / "stack.h").write_text("typedef struct Stack Stack;\n", encoding="utf-8")

            with mock.patch(
                "wiki.static_site.render_darcsit",
                return_value='<a href=ast/stack.h>stack.h</a><a href=ast/ast.h#Ast>Ast</a>',
            ):
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        artifact_base_url="/artifacts",
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                ).build()

            rendered = (output / "src" / "ast" / "ast.h" / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="/src/ast/stack.h/"', rendered)
            self.assertIn('href="/src/ast/ast.h/#Ast"', rendered)
            self.assertNotIn("/artifacts/src/ast/ast/stack.h", rendered)

    def test_cli_build_invocation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# CLI Front\n", "Create front")

            status = main(
                [
                    "--base-dir",
                    str(root),
                    "--wiki-root",
                    str(wiki_root),
                    "build",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(status, 0)
            self.assertIn("CLI Front", (output / "index.html").read_text(encoding="utf-8"))

    def test_cli_reads_gititpy_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "content"
            output = root / "site"
            WikiRepository(wiki_root).write_page("FrontPage", "# Config Front\n", "Create front")
            (root / "gititpy.toml").write_text(
                """
[site]
title = "Configured Site"
base_url = "/docs"

[paths]
wiki_root = "content"
output = "site"

[build]
source = false
jobs = 1
""",
                encoding="utf-8",
            )

            status = main(["--base-dir", str(root), "build"])

            self.assertEqual(status, 0)
            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Config Front", rendered)
            self.assertIn("Configured Site", rendered)
            self.assertIn('href="/docs/_index.html"', rendered)

    def test_cli_arguments_override_gititpy_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configured_root = root / "configured"
            override_root = root / "override"
            output = root / "site"
            WikiRepository(configured_root).write_page("FrontPage", "# Configured\n", "Create front")
            WikiRepository(override_root).write_page("FrontPage", "# Overridden\n", "Create front")
            (root / "gititpy.toml").write_text(
                """
[paths]
wiki_root = "configured"
output = "site"
""",
                encoding="utf-8",
            )

            status = main(
                [
                    "--base-dir",
                    str(root),
                    "--wiki-root",
                    str(override_root),
                    "build",
                ]
            )

            self.assertEqual(status, 0)
            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Overridden", rendered)
            self.assertNotIn("Configured", rendered)

    def test_cli_verbose_build_prints_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Verbose Front\n", "Create front")

            stdout = StringIO()
            with redirect_stdout(stdout):
                status = main(
                    [
                        "--base-dir",
                        str(root),
                        "--wiki-root",
                        str(wiki_root),
                        "build",
                        "--output",
                        str(output),
                        "--no-source",
                        "--verbose",
                    ]
                )

            self.assertEqual(status, 0)
            output_text = stdout.getvalue()
            self.assertIn("Rendering wiki pages", output_text)
            self.assertIn("Writing search index", output_text)
            self.assertIn(str(output / "index.html"), output_text)

    def test_static_build_uses_site_template_and_static_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            output = root / "public"
            template_root = root / "templates"
            static_root = root / "static"
            WikiRepository(wiki_root).write_page("FrontPage", "# Template Front\n", "Create front")
            (template_root / "wiki").mkdir(parents=True)
            (template_root / "wiki" / "page.html").write_text(
                "<!doctype html><title>{{ wiki_title }}</title><main>{{ content_html|safe }}</main>",
                encoding="utf-8",
            )
            (static_root / "wiki" / "css").mkdir(parents=True)
            (static_root / "wiki" / "css" / "custom.css").write_text(
                "body { color: rgb(1, 2, 3); }",
                encoding="utf-8",
            )

            builder = StaticSiteBuilder(
                config=SiteConfig(
                    base_dir=root,
                    wiki_root=wiki_root,
                    template_roots=(template_root,),
                    static_roots=(static_root,),
                ),
                output_dir=output,
            )
            builder.build()

            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("<main>", rendered)
            self.assertIn("Template Front", rendered)
            self.assertEqual(
                (output / "static" / "wiki" / "css" / "custom.css").read_text(encoding="utf-8"),
                "body { color: rgb(1, 2, 3); }",
            )


if __name__ == "__main__":
    unittest.main()
