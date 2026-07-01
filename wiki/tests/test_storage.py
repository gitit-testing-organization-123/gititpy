import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock
from gititpy.artifacts_cli import main as artifacts_main
from gititpy.cli import main
from gititpy.config import SiteConfig
from wiki.artifacts import ArtifactRoot, discover_artifact_jobs
from wiki.bibliography import render_bibliography_html
from wiki.darcsit import DarcsitHelpers, render as render_markdown
from wiki.plots import expected_plot_artifacts, gnuplot_script_from_source, python_script_from_source
from wiki.site import StaticSiteBuilder
from wiki.storage import PageNameError, WikiRepository
from wiki.tags import QccTagsResult, generate_qcc_tags

class StorageTests(unittest.TestCase):

    def test_front_page_is_seeded_as_plain_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = WikiRepository(Path(tmpdir) / 'pages')
            source = repo.read_page('Front Page')
        self.assertIn('Welcome to GititPy', source)

    def test_page_can_be_written_without_git_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'pages'
            repo = WikiRepository(root)
            repo.write_page('TestPage', '# Test Page\n\nHello from a test.')
            self.assertFalse((root / '.git').exists())
            self.assertIn('Hello from a test.', repo.read_page('TestPage'))

    def test_wiki_folder_lists_child_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'pages'
            repo = WikiRepository(root)
            repo.write_page('sandbox/PageOne', '# Page One\n')
            repo.write_page('sandbox/nested/PageTwo', '# Page Two\n')
            entries = repo.list_directory('sandbox')
        self.assertEqual([entry.name for entry in entries], ['nested', 'PageOne'])
        self.assertTrue(entries[0].is_dir)
        self.assertEqual(entries[0].slug, 'sandbox/nested/')

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PageNameError):
            WikiRepository(Path('/tmp/pages')).normalize_slug('../secret')

    def test_source_page_uses_exact_extension_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'pages'
            repo = WikiRepository(root)
            repo.write_page('examples/hello.c', 'int main(void) { return 0; }\n')
            self.assertTrue((root / 'examples' / 'hello.c').exists())
            self.assertFalse((root / 'examples' / 'hello.c.md').exists())
            self.assertEqual(repo.read_page('examples/hello.c'), 'int main(void) { return 0; }\n')

    def test_existing_page_file_with_spaces_and_page_suffix_round_trips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'pages'
            root.mkdir()
            path = root / 'Gitit User’s Guide.page'
            path.write_text('# User Guide\n', encoding='utf-8')
            repo = WikiRepository(root)
            slug = repo.page_slug_for_path(path.relative_to(root))
            self.assertEqual(slug, 'Gitit User’s Guide')
            self.assertEqual(repo.read_page(slug), '# User Guide\n')
            self.assertEqual(repo.page_path(slug), path)
