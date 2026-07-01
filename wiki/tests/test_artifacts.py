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

class ArtifactTests(unittest.TestCase):

    def test_artifact_detector_finds_basilisk_sibling_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'src'
            artifact_dir = root / 'test' / 'vortex'
            artifact_dir.mkdir(parents=True)
            (root / 'test' / 'vortex.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (artifact_dir / 'plot.png').write_bytes(b'png')
            (artifact_dir / 'plot.png.tags').write_text('decl x y z\n', encoding='utf-8')
            jobs = discover_artifact_jobs([ArtifactRoot('source', root)])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].source_rel, 'test/vortex.c')
            self.assertEqual(jobs[0].artifact_rel_dir, 'test/vortex')
            self.assertEqual(jobs[0].existing_artifacts, ('plot.png', 'plot.png.tags'))

    def test_artifact_detector_can_use_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'src'
            artifact_root = Path(tmpdir) / 'build' / 'src'
            artifact_dir = artifact_root / 'test' / 'vortex'
            (root / 'test').mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (root / 'test' / 'vortex.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (artifact_dir / 'movie.mp4').write_bytes(b'movie')
            jobs = discover_artifact_jobs([ArtifactRoot('source', root, artifact_root)])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].artifact_dir, artifact_dir)
            self.assertEqual(jobs[0].existing_artifacts, ('movie.mp4',))

    def test_artifact_detector_finds_references_without_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'src'
            (root / 'examples').mkdir(parents=True)
            (root / 'examples' / 'bubble.c').write_text('/**\n        ![Plot](bubble/plot.png)\n        <video src="/artifacts/examples/bubble/movie.mp4"></video>\n        [Directory](bubble/1024/)\n         */\n        int main(void) { return 0; }\n        ', encoding='utf-8')
            jobs = discover_artifact_jobs([ArtifactRoot('source', root)])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].referenced_artifacts, ('movie.mp4', 'plot.png'))

    def test_artifacts_cli_lists_detected_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_dir = source_root / 'test' / 'vortex'
            artifact_dir.mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (artifact_dir / 'plot.png').write_bytes(b'png')
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = artifacts_main(['--base-dir', str(root), 'list'])
            self.assertEqual(status, 0)
            self.assertIn('source:test/vortex.c', stdout.getvalue())
            self.assertIn('src/test/vortex/plot.png', stdout.getvalue())

    def test_artifacts_cli_stages_from_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            artifact_dir = artifact_root / 'test' / 'vortex'
            generated_artifact_dir = artifact_root / 'test' / 'dirichlet'
            stage_dir = root / 'stage'
            (source_root / 'test').mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            generated_artifact_dir.mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text('/**\n![Plot](vortex/plot.png)\n*/\nint main(void) { return 0; }\n', encoding='utf-8')
            (artifact_dir / 'plot.png').write_bytes(b'png')
            (artifact_dir / 'log').write_text('log\n', encoding='utf-8')
            (artifact_dir / 'dump').write_bytes(b'dump')
            (artifact_dir / 'vortex').write_bytes(b'binary')
            (artifact_dir / 'linked.png').symlink_to(artifact_dir / 'plot.png')
            (generated_artifact_dir / 'dirichlet').write_bytes(b'binary')
            (generated_artifact_dir / 'a.png').write_bytes(b'a-png')
            (generated_artifact_dir / 'dump').write_bytes(b'dump')
            (artifact_root / 'CMakeFiles').mkdir()
            (artifact_root / 'CMakeFiles' / 'noise.txt').write_text('noise\n', encoding='utf-8')
            status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(artifact_root), 'stage', '--dest', str(stage_dir)])
            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / 'src' / 'test' / 'vortex' / 'plot.png').read_bytes(), b'png')
            self.assertEqual((stage_dir / 'src' / 'test' / 'vortex' / 'log').read_text(encoding='utf-8'), 'log\n')
            self.assertEqual((stage_dir / 'src' / 'test' / 'vortex' / 'linked.png').read_bytes(), b'png')
            self.assertEqual((stage_dir / 'src' / 'test' / 'dirichlet' / 'a.png').read_bytes(), b'a-png')
            self.assertFalse((stage_dir / 'src' / 'test' / 'vortex' / 'dump').exists())
            self.assertFalse((stage_dir / 'src' / 'test' / 'vortex' / 'vortex').exists())
            self.assertFalse((stage_dir / 'src' / 'test' / 'dirichlet' / 'dump').exists())
            self.assertFalse((stage_dir / 'src' / 'test' / 'dirichlet' / 'dirichlet').exists())
            self.assertFalse((stage_dir / 'src' / 'CMakeFiles' / 'noise.txt').exists())

    def test_artifacts_cli_stages_sandbox_from_separate_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            sandbox_root = root / 'sandbox'
            sandbox_artifact_root = root / 'sandbox-build'
            source_artifact_dir = source_artifact_root / 'test' / 'vortex'
            sandbox_artifact_dir = sandbox_artifact_root / 'cases' / 'drop'
            stage_dir = root / 'stage'
            (sandbox_root / 'cases').mkdir(parents=True)
            source_artifact_dir.mkdir(parents=True)
            sandbox_artifact_dir.mkdir(parents=True)
            (sandbox_root / 'cases' / 'drop.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (source_artifact_dir / 'vortex').write_bytes(b'binary')
            (source_artifact_dir / 'plot.png').write_bytes(b'source-png')
            (sandbox_artifact_dir / 'drop').write_bytes(b'binary')
            (sandbox_artifact_dir / 'plot.png').write_bytes(b'sandbox-png')
            status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(source_artifact_root), '--sandbox-root', str(sandbox_root), '--sandbox-artifact-root', str(sandbox_artifact_root), '--scope', 'sandbox', 'stage', '--dest', str(stage_dir)])
            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / 'sandbox' / 'cases' / 'drop' / 'plot.png').read_bytes(), b'sandbox-png')
            self.assertFalse((stage_dir / 'sandbox' / 'cases' / 'drop' / 'drop').exists())
            self.assertFalse((stage_dir / 'src' / 'test' / 'vortex' / 'plot.png').exists())

    def test_artifacts_cli_stages_source_and_sandbox_roots_disjointly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_root = root / 'basilisk' / 'src'
            source_artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            sandbox_root = root / 'sandbox'
            sandbox_artifact_root = root / 'sandbox-build'
            source_artifact_dir = source_artifact_root / 'test' / 'vortex'
            sandbox_artifact_dir = sandbox_artifact_root / 'cases' / 'drop'
            stage_dir = root / 'stage'
            (source_root / 'test').mkdir(parents=True)
            (sandbox_root / 'cases').mkdir(parents=True)
            source_artifact_dir.mkdir(parents=True)
            sandbox_artifact_dir.mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (sandbox_root / 'cases' / 'drop.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (source_artifact_dir / 'vortex').write_bytes(b'binary')
            (source_artifact_dir / 'plot.png').write_bytes(b'source-png')
            (sandbox_artifact_dir / 'drop').write_bytes(b'binary')
            (sandbox_artifact_dir / 'plot.png').write_bytes(b'sandbox-png')
            status = artifacts_main(['--base-dir', str(root), '--artifact-root', str(source_artifact_root), '--sandbox-root', str(sandbox_root), '--sandbox-artifact-root', str(sandbox_artifact_root), 'stage', '--dest', str(stage_dir)])
            self.assertEqual(status, 0)
            self.assertEqual((stage_dir / 'src' / 'test' / 'vortex' / 'plot.png').read_bytes(), b'source-png')
            self.assertEqual((stage_dir / 'sandbox' / 'cases' / 'drop' / 'plot.png').read_bytes(), b'sandbox-png')
