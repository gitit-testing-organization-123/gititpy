import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from gititpy.cli import main
from gititpy.config import SiteConfig
from wiki.bibliography import render_bibliography_html
from wiki.darcsit import DarcsitHelpers, render as render_markdown, source_to_markdown
from wiki.static_site import StaticSiteBuilder
from wiki.storage import PageNameError, WikiRepository


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
        self.assertNotIn("--katex", args)

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

    def test_source_render_failure_falls_back_to_plain_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / "pages"
            source_root = root / "basilisk" / "src"
            output = root / "public"
            WikiRepository(wiki_root).write_page("FrontPage", "# Front\n", "Create front")
            source_root.mkdir(parents=True)
            (source_root / "generated.c").write_text("int generated(void) { return 1; }\n", encoding="utf-8")

            def render_or_fail(source, slug="", source_path=None):
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


if __name__ == "__main__":
    unittest.main()
