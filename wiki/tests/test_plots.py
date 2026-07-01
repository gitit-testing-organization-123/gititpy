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

class PlotArtifactTests(unittest.TestCase):

    def test_artifact_detector_finds_derived_gnuplot_outputs(self):
        source = "/**\n        ~~~gnuplot Default plot\n        plot 'out'\n        ~~~\n\n        ~~~gnuplot PNG plot\n        set output 'plot.png'\n        plot 'out'\n        ~~~\n         */\n        int main(void) { return 0; }\n        "
        self.assertEqual(expected_plot_artifacts(source), ('_plot0.svg', 'plot.png'))
        script = gnuplot_script_from_source(source)
        self.assertIn("set output '_plot0.svg';", script)
        self.assertIn("set output 'plot.png'", script)
        self.assertIn('mogrify -trim plot.png', script)
        self.assertEqual(python_script_from_source(source), '')

    def test_artifacts_cli_lists_derived_plot_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_dir = source_root / 'test' / 'vortex'
            (source_root / 'test').mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text("/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n", encoding='utf-8')
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(['--base-dir', str(root), 'plots', 'list'])
            self.assertEqual(status, 0)
            self.assertIn('source:test/vortex.c', stdout.getvalue())
            self.assertIn('src/test/vortex/plot.png', stdout.getvalue())

    def test_artifacts_cli_skips_derived_plot_artifacts_without_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text("/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n", encoding='utf-8')
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(['--base-dir', str(root), 'plots', 'list'])
            self.assertEqual(status, 0)
            self.assertNotIn('source:test/vortex.c', stdout.getvalue())
            self.assertNotIn('src/test/vortex/plot.png', stdout.getvalue())

    def test_artifacts_cli_generates_plot_scripts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            artifact_dir = artifact_root / 'test' / 'vortex'
            (source_root / 'test').mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text("/**\n        ~~~gnuplot Plot\n        set output 'plot.png'\n        plot 'out'\n        ~~~\n\n        ~~~pythonplot Py plot\n        import matplotlib.pyplot as plt\n        plt.savefig('py.png')\n        ~~~\n         */\n        int main(void) { return 0; }\n        ", encoding='utf-8')
            completed = mock.Mock(returncode=0, stdout='', stderr='')
            with mock.patch('wiki.plots.subprocess.run', return_value=completed) as run:
                status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(artifact_root), 'plots', 'generate', '--gnuplot-command', 'gnuplot', '--python-command', 'python'])
            self.assertEqual(status, 0)
            self.assertIn("set output 'plot.png'", (artifact_dir / 'plots').read_text(encoding='utf-8'))
            self.assertIn("plt.savefig('py.png')", (artifact_dir / 'plots.py').read_text(encoding='utf-8'))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][0], 'gnuplot')
            self.assertIn('set term svg enhanced', run.call_args_list[0].args[0][2])
            self.assertNotIn('set term @SVG', run.call_args_list[0].args[0][2])
            self.assertEqual(run.call_args_list[0].kwargs['cwd'], artifact_dir)
            self.assertEqual(run.call_args_list[1].args[0], ['python', 'plots.py'])
            self.assertEqual(run.call_args_list[1].kwargs['cwd'], artifact_dir)

    def test_artifacts_cli_links_source_plot_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            artifact_dir = artifact_root / 'test' / 'vortex'
            source_dir = source_root / 'test'
            source_aux_dir = source_dir / 'vortex'
            source_dir.mkdir(parents=True)
            source_aux_dir.mkdir()
            artifact_dir.mkdir(parents=True)
            (source_dir / 'vortex.c').write_text("/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'vortex.ref', 'profile.dat'\n~~~\n*/\n", encoding='utf-8')
            (source_dir / 'vortex.ref').write_text('1 2\n', encoding='utf-8')
            (source_aux_dir / 'profile.dat').write_text('1 3\n', encoding='utf-8')
            (source_aux_dir / '.hidden').write_text('hidden\n', encoding='utf-8')
            completed = mock.Mock(returncode=0, stdout='', stderr='')
            with mock.patch('wiki.plots.subprocess.run', return_value=completed):
                status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(artifact_root), 'plots', 'generate'])
            self.assertEqual(status, 0)
            self.assertTrue((artifact_dir / 'vortex.ref').is_symlink())
            self.assertEqual((artifact_dir / 'vortex.ref').resolve(), (source_dir / 'vortex.ref').resolve())
            self.assertTrue((artifact_dir / 'profile.dat').is_symlink())
            self.assertEqual((artifact_dir / 'profile.dat').resolve(), (source_aux_dir / 'profile.dat').resolve())
            self.assertFalse((artifact_dir / '.hidden').exists())

    def test_artifacts_cli_does_not_generate_plots_without_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            artifact_dir = artifact_root / 'test' / 'vortex'
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text("/**\n~~~gnuplot Plot\nset output 'plot.png'\nplot 'out'\n~~~\n*/\n", encoding='utf-8')
            stdout = StringIO()
            with mock.patch('wiki.plots.subprocess.run') as run, redirect_stdout(stdout):
                status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(artifact_root), 'plots', 'generate'])
            self.assertEqual(status, 0)
            self.assertIn('Generated plot artifacts for 0 source file(s)', stdout.getvalue())
            self.assertFalse(artifact_dir.exists())
            run.assert_not_called()
