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

class BibliographyTests(unittest.TestCase):

    def test_python_bibliography_renderer_formats_bibtex(self):
        rendered = render_bibliography_html('@article{doe2024,\n          title = {A Bibliography Test},\n          author = {Doe, Jane},\n          journal = {Journal of Tests},\n          year = {2024}\n        }\n        ')
        self.assertIn('bibtex', rendered)
        self.assertIn('Jane Doe', rendered)
        self.assertIn('A Bibliography Test', rendered)

    def test_python_bibliography_renderer_expands_hal_entries(self):
        hal_response = BytesIO(b'@article{serverkey,\n          title = {HAL Result},\n          author = {Doe, Jane},\n          year = {2024}\n        }\n        ')
        with mock.patch('wiki.bibliography.urlopen', return_value=hal_response):
            rendered = render_bibliography_html('@hal{localkey, hal-123456}\n')
        self.assertIn('name="localkey"', rendered)
        self.assertIn('HAL Result', rendered)

    def test_c_page_renders_bibliography_block(self):
        source = '/**\n        # Bibliography page\n\n        ~~~bib\n        @article{doe2024,\n          title = {A Bibliography Test},\n          author = {Doe, Jane},\n          journal = {Journal of Tests},\n          year = {2024}\n        }\n        ~~~\n         */\n        int main(void) {\n            return 0;\n        }\n        '
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'bib.c'
            path.write_text(source, encoding='utf-8')
            rendered = render_markdown(source, 'bib.c', source_path=path)
        self.assertIn('bibtex', rendered)
        self.assertIn('A Bibliography Test', rendered)
