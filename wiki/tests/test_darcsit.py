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
from wiki.darcsit import DarcsitHelpers, darcsit_environment, render as render_markdown
from wiki.plots import expected_plot_artifacts, gnuplot_script_from_source, python_script_from_source
from wiki.site import StaticSiteBuilder
from wiki.storage import PageNameError, WikiRepository
from wiki.tags import QccTagsResult, generate_qcc_tags

class DarcsitRenderingTests(unittest.TestCase):

    def test_markdown_rendering_prefers_pandoc(self):
        completed = mock.Mock(stdout='<h1>Pandoc rendered</h1>\n')
        with mock.patch('wiki.darcsit.shutil.which', return_value='/nix/store/bin/pandoc'):
            with mock.patch('wiki.darcsit.subprocess.run', return_value=completed) as run:
                rendered = render_markdown('# Ignored')
        self.assertEqual(rendered, '<h1>Pandoc rendered</h1>\n')
        pandoc_calls = [call_args for call_args in run.call_args_list if call_args.args[0][0] == '/nix/store/bin/pandoc']
        self.assertEqual(len(pandoc_calls), 1)
        args = pandoc_calls[0].args[0]
        self.assertIn('--mathjax', args)
        self.assertIn('--toc', args)
        self.assertNotIn('--katex', args)

    def test_markdown_rendering_can_disable_toc_with_metadata(self):
        completed = mock.Mock(stdout='<h1>No TOC</h1>\n')
        source = '---\n        toc: no\n        ...\n\n        # No TOC\n        '
        with mock.patch('wiki.darcsit.shutil.which', return_value='/nix/store/bin/pandoc'):
            with mock.patch('wiki.darcsit.subprocess.run', return_value=completed) as run:
                render_markdown(source)
        args = run.call_args.args[0]
        self.assertNotIn('--toc', args)

    def test_markdown_rendering_requires_pandoc(self):
        with mock.patch('wiki.darcsit.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Pandoc is required'):
                render_markdown('# Pandoc required')

    def test_source_rendering_requires_packaged_darcsit_helpers(self):
        with mock.patch('wiki.darcsit.DarcsitHelpers.available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Packaged Darcsit helper'):
                render_markdown('int main(void) { return 0; }\n', 'missing.c')

    def test_c_page_renders_through_darcsit_helpers(self):
        source = '/**\n        # Helper page\n\n        Helper prose.\n         */\n        int main(void) {\n            return 0;\n        }\n        '
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'helper.c'
            path.write_text(source, encoding='utf-8')
            rendered = render_markdown(source, 'helper.c', source_path=path)
        self.assertIn('Helper prose', rendered)
        self.assertIn('sourceCode', rendered)
        self.assertIn('id=6', rendered)

    def test_cmakelists_renders_as_whole_file_code(self):
        source = 'cmake_minimum_required(VERSION 3.20)\nproject(example)\n'
        with mock.patch('wiki.darcsit.DarcsitHelpers.pagemagic') as pagemagic:
            with mock.patch('wiki.darcsit.render_markdown', return_value='<pre>CMake</pre>') as markdown:
                rendered = render_markdown(source, 'CMakeLists.txt')

        self.assertEqual(rendered, '<pre>CMake</pre>')
        pagemagic.assert_not_called()
        self.assertTrue(markdown.call_args.args[0].startswith('~~~cmake\n'))

    def test_extensionless_shell_script_renders_as_whole_file_code(self):
        source = '#!/usr/bin/env bash\n/**\n# Not literate prose\n*/\necho ok\n'
        with mock.patch('wiki.darcsit.DarcsitHelpers.pagemagic') as pagemagic:
            with mock.patch('wiki.darcsit.render_markdown', return_value='<pre>Shell</pre>') as markdown:
                rendered = render_markdown(source, 'dotest')

        self.assertEqual(rendered, '<pre>Shell</pre>')
        pagemagic.assert_not_called()
        self.assertTrue(markdown.call_args.args[0].startswith('~~~bash\n'))

    def test_markdown_page_renders_table_of_contents(self):
        source = '# Page\n\n## Section One\n\nText.\n\n## Section Two\n\nMore text.\n'
        rendered = render_markdown(source)
        self.assertIn('id="TOC"', rendered)
        self.assertIn('Section One', rendered)
        self.assertIn('section-one', rendered)

    def test_packaged_darcsit_helpers_are_preferred(self):
        helpers = DarcsitHelpers()
        self.assertTrue(helpers.available())
        self.assertEqual(helpers.root.name, 'bin')
        self.assertEqual(helpers.root.parent.name, 'darcsit_helpers')

    def test_darcsit_environment_sets_basilisk_url_for_tag_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = darcsit_environment(Path(tmpdir) / 'src', 'https://example.org/wiki/')
        self.assertEqual(env['BASILISK'], str(Path(tmpdir) / 'src'))
        self.assertEqual(env['HTTP_BASILISK_URL'], 'https://example.org/wiki')

    def test_packaged_literate_helper_does_not_stamp_asset_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'test.c'
            source_path.write_text('/**\n~~~gnuplot Plot\nplot "out"\n~~~\n\n![Figure](figure.png)\n*/\nint main(void) { return 0; }\n', encoding='utf-8')
            rendered = DarcsitHelpers().literate(str(source_path), page_magic=True)
        self.assertIsNotNone(rendered)
        self.assertIn('_plot0.svg)', rendered)
        self.assertIn('![Figure](figure.png)', rendered)
        self.assertNotIn('_plot0.svg?', rendered)
        self.assertNotIn('figure.png?', rendered)
