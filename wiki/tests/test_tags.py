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

class TagGenerationTests(unittest.TestCase):

    def test_qcc_tags_generation_sets_basilisk_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_root = Path(tmpdir) / 'basilisk' / 'src'
            source_root.mkdir(parents=True)
            source_path = source_root / 'example.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            completed = mock.Mock(returncode=0, stdout='', stderr='')
            with mock.patch('wiki.tags.shutil.which', return_value='/nix/store/bin/qcc'):
                with mock.patch('wiki.tags.subprocess.run', return_value=completed) as run:
                    result = generate_qcc_tags(source_path, source_root)
            self.assertTrue(result.generated)
            self.assertEqual(run.call_args.args[0], ['/nix/store/bin/qcc', '-tags', 'example.c'])
            self.assertEqual(run.call_args.kwargs['cwd'], str(source_root.resolve()))
            self.assertEqual(run.call_args.kwargs['env']['BASILISK'], str(source_root))
            self.assertEqual(run.call_args.kwargs['env']['BASILISK_INCLUDE_PATH'], str(source_root))

    def test_qcc_tags_generation_uses_source_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ('long-source-root-' + 'x' * 80)
            source_root = root / 'basilisk' / 'src'
            source_path = source_root / 'gotm' / 'turbulence' / 'r_ratio.h'
            source_path.parent.mkdir(parents=True)
            source_path.write_text('static inline void turbulence_r_ratio (void) {}\n', encoding='utf-8')
            completed = mock.Mock(returncode=0, stdout='', stderr='')
            with mock.patch('wiki.tags.shutil.which', return_value='/nix/store/bin/qcc'):
                with mock.patch('wiki.tags.subprocess.run', return_value=completed) as run:
                    result = generate_qcc_tags(source_path, source_root)
            self.assertTrue(result.generated)
            self.assertEqual(run.call_args.args[0], ['/nix/store/bin/qcc', '-tags', 'gotm/turbulence/r_ratio.h'])
            self.assertLess(len(run.call_args.args[0][2] + '.tags'), 80)

    def test_qcc_tags_generation_reports_missing_qcc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_root = Path(tmpdir)
            source_path = source_root / 'example.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            with mock.patch('wiki.tags.shutil.which', return_value=None):
                result = generate_qcc_tags(source_path, source_root)
            self.assertIn('qcc command not found', result.warning)
