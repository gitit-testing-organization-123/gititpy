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

class CliTests(unittest.TestCase):

    def test_cli_build_invocation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# CLI Front\n')
            status = main(['--base-dir', str(root), '--wiki-root', str(wiki_root), 'build', '--output', str(output)])
            self.assertEqual(status, 0)
            self.assertIn('CLI Front', (output / 'index.html').read_text(encoding='utf-8'))

    def test_cli_reads_gititpy_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'content'
            output = root / 'site'
            WikiRepository(wiki_root).write_page('FrontPage', '# Config Front\n')
            (root / 'gititpy.toml').write_text('\n        [site]\n        title = "Configured Site"\n        base_url = "/docs"\n\n        [paths]\n        wiki_root = "content"\n        output = "site"\n\n        [build]\n        source = false\n        jobs = 1\n        ', encoding='utf-8')
            status = main(['--base-dir', str(root), 'build'])
            self.assertEqual(status, 0)
            rendered = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('Config Front', rendered)
            self.assertIn('Configured Site', rendered)
            self.assertIn('href="/docs/_index.html"', rendered)

    def test_cli_arguments_override_gititpy_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configured_root = root / 'configured'
            override_root = root / 'override'
            output = root / 'site'
            WikiRepository(configured_root).write_page('FrontPage', '# Configured\n')
            WikiRepository(override_root).write_page('FrontPage', '# Overridden\n')
            (root / 'gititpy.toml').write_text('\n        [paths]\n        wiki_root = "configured"\n        output = "site"\n        ', encoding='utf-8')
            status = main(['--base-dir', str(root), '--wiki-root', str(override_root), 'build'])
            self.assertEqual(status, 0)
            rendered = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('Overridden', rendered)
            self.assertNotIn('Configured', rendered)

    def test_cli_verbose_build_prints_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Verbose Front\n')
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = main(['--base-dir', str(root), '--wiki-root', str(wiki_root), 'build', '--output', str(output), '--no-source', '--verbose'])
            self.assertEqual(status, 0)
            output_text = stdout.getvalue()
            self.assertIn('Rendering wiki pages', output_text)
            self.assertIn('Writing search index', output_text)
            self.assertIn(str(output / 'index.html'), output_text)
