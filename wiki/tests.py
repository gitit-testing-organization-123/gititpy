import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase

from .darcsit import DarcsitHelpers, source_to_markdown
from .storage import WikiRepository
from .views import render_markdown


class WikiViewsTests(SimpleTestCase):
    def test_front_page_is_seeded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(WIKI_ROOT=Path(tmpdir) / "pages"):
                response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome to GititPy")

    def test_page_can_be_created_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(WIKI_ROOT=Path(tmpdir) / "pages"):
                client = Client()
                response = client.post(
                    "/_edit/TestPage",
                    {
                        "content": "# Test Page\n\nHello from a test.",
                        "message": "Create TestPage",
                    },
                )
                self.assertEqual(response.status_code, 302)
                response = client.get("/TestPage")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello from a test.")

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(WIKI_ROOT=Path(tmpdir) / "pages"):
                response = Client().get("/_edit/../secret")

        self.assertEqual(response.status_code, 400)

    def test_markdown_rendering_prefers_pandoc(self):
        completed = mock.Mock(stdout="<h1>Pandoc rendered</h1>\n")
        with mock.patch("wiki.darcsit.shutil.which", return_value="/nix/store/bin/pandoc"):
            with mock.patch("wiki.darcsit.subprocess.run", return_value=completed) as run:
                rendered = render_markdown("# Ignored")

        self.assertEqual(rendered, "<h1>Pandoc rendered</h1>\n")
        run.assert_called_once()
        args = run.call_args.args[0]
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

    def test_pages_load_mathjax(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(WIKI_ROOT=Path(tmpdir) / "pages"):
                response = Client().get("/")

        self.assertContains(response, "MathJax")
        self.assertContains(response, "tex-chtml.js")
