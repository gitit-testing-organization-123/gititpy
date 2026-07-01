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

class StaticLinkTests(unittest.TestCase):

    def test_static_build_rewrites_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            rendered_content = '<a href="/artifacts/examples/bubble/movie.mp4">movie</a><img src="/artifacts/examples/bubble/plot 1.png?download=1#frame"><video poster="/artifacts/examples/bubble/poster.png" src="/local.mp4"></video><a href="/Help.html">help</a>'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False, artifact_base_url='https://artifacts.example.org/site'), output_dir=output).build()
            rendered = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="https://artifacts.example.org/site/examples/bubble/movie.mp4"', rendered)
            self.assertIn('src="https://artifacts.example.org/site/examples/bubble/plot%201.png?download=1#frame"', rendered)
            self.assertIn('poster="https://artifacts.example.org/site/examples/bubble/poster.png"', rendered)
            self.assertIn('href="/Help/"', rendered)
            self.assertIn('src="/local.mp4"', rendered)

    def test_static_source_render_rewrites_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'vortex.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            rendered_content = '<a href="vortex/vort.mp4">movie</a><img src="vortex/plot 1.png?download=1#frame"><a href="other/movie.mp4">other</a>'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='https://artifacts.example.org/site', generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'test' / 'vortex.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="https://artifacts.example.org/site/src/test/vortex/vort.mp4"', rendered)
            self.assertIn('src="https://artifacts.example.org/site/src/test/vortex/plot%201.png?download=1#frame"', rendered)
            self.assertIn('href="other/movie.mp4"', rendered)

    def test_static_source_render_rewrites_cross_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            artifact_root = root / 'basilisk' / 'build' / 'release' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'neumann.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (artifact_root / 'test' / 'dirichlet').mkdir(parents=True)
            (artifact_root / 'test' / 'dirichlet' / 'a.png').write_bytes(b'png')
            rendered_content = '<img src="dirichlet/a.png?1782833204"><a href="other/movie.mp4">other</a>'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='/artifacts', artifact_root=artifact_root, generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'test' / 'neumann.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/src/test/dirichlet/a.png?1782833204"', rendered)
            self.assertIn('href="other/movie.mp4"', rendered)

    def test_static_sandbox_render_rewrites_cross_sibling_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            sandbox_artifact_root = root / 'sandbox-build'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (sandbox_root / 'cases').mkdir(parents=True)
            (sandbox_root / 'cases' / 'neumann.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (sandbox_artifact_root / 'cases' / 'dirichlet').mkdir(parents=True)
            (sandbox_artifact_root / 'cases' / 'dirichlet' / 'a.png').write_bytes(b'png')
            rendered_content = '<img src="dirichlet/a.png?1782833204">'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, sandbox_artifact_root=sandbox_artifact_root, artifact_base_url='/artifacts', build_source=False, jobs=1), output_dir=output).build()
            rendered = (output / 'sandbox' / 'cases' / 'neumann.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/sandbox/cases/dirichlet/a.png?1782833204"', rendered)

    def test_static_sandbox_render_rewrites_absolute_sandbox_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (sandbox_root / 'cases').mkdir(parents=True)
            source_path = sandbox_root / 'cases' / 'stokes.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            rendered_content = f'<img src="{sandbox_root}/cases/stokes/_plot0.svg?1782798642">'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, artifact_base_url='/artifacts', build_source=False, generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'sandbox' / 'cases' / 'stokes.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/sandbox/cases/stokes/_plot0.svg?1782798642"', rendered)
            self.assertNotIn(str(sandbox_root), rendered)

    def test_static_source_render_rewrites_absolute_source_artifact_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'test').mkdir(parents=True)
            source_path = source_root / 'test' / 'stokes.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            rendered_content = f'<img src="{source_root}/test/stokes/_plot0.svg?1782798642">'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='/artifacts', generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'test' / 'stokes.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/src/test/stokes/_plot0.svg?1782798642"', rendered)
            self.assertNotIn(str(source_root), rendered)

    def test_static_source_render_rewrites_temp_plot_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'stokes.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            rendered_content = '<img src="/tmp/tmpabc123/_plot0.svg?1782798642">'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='/artifacts', generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'test' / 'stokes.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/src/test/stokes/_plot0.svg?1782798642"', rendered)
            self.assertNotIn('/tmp/tmpabc123', rendered)

    def test_static_source_render_rewrites_named_temp_plot_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'test').mkdir(parents=True)
            (source_root / 'test' / 'static_bubble.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            rendered_content = '<img src="/tmp/tmp3y5ywol9/p001cbt.svg?1782840608">'
            with mock.patch('wiki.site.render_darcsit', return_value=rendered_content):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='/artifacts', generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'test' / 'static_bubble.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('src="/artifacts/src/test/static_bubble/p001cbt.svg?1782840608"', rendered)
            self.assertNotIn('/tmp/tmp3y5ywol9', rendered)

    def test_static_source_render_generates_qcc_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            source_root.mkdir(parents=True)
            source_path = source_root / 'example.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<p>Rendered</p>'):
                with mock.patch('wiki.site.generate_qcc_tags', return_value=QccTagsResult(generated=True)) as generate:
                    StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1), output_dir=output).build()
            generate.assert_called_once_with(
                source_path,
                source_root,
                'qcc',
                basilisk_root=source_root,
                include_roots=(source_root,),
            )

    def test_static_sandbox_render_generates_qcc_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            sandbox_root.mkdir(parents=True)
            source_path = sandbox_root / 'example.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<p>Rendered</p>'):
                with mock.patch('wiki.site.generate_qcc_tags', return_value=QccTagsResult(generated=True)) as generate:
                    StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False, jobs=1), output_dir=output).build()
            generate.assert_called_once_with(
                source_path,
                sandbox_root,
                'qcc',
                basilisk_root=sandbox_root,
                include_roots=(sandbox_root,),
            )

    def test_static_sandbox_tags_use_source_tree_as_basilisk_root_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            sandbox_root.mkdir(parents=True)
            source_root.mkdir(parents=True)
            source_path = sandbox_root / 'example.c'
            source_path.write_text('int main(void) { return 0; }\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<p>Rendered</p>'):
                with mock.patch('wiki.site.generate_qcc_tags', return_value=QccTagsResult(generated=True)) as generate:
                    StaticSiteBuilder(
                        config=SiteConfig(
                            base_dir=root,
                            wiki_root=wiki_root,
                            sandbox_root=sandbox_root,
                            source_root=source_root,
                            jobs=1,
                        ),
                        output_dir=output,
                    ).build()
            generate.assert_called_once_with(
                source_path,
                sandbox_root,
                'qcc',
                basilisk_root=source_root,
                include_roots=(sandbox_root, source_root),
            )

    def test_relative_source_links_rewrite_to_absolute_source_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'all-mach.h').write_text('/**\n        [Poisson](poisson.h)\n         */\n        ', encoding='utf-8')
            (source_root / 'poisson.h').write_text('int poisson(void);\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<a href="poisson.h">Poisson</a>'):
                builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1), output_dir=output)
                builder.build()
            rendered = (output / 'src' / 'all-mach.h' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/src/poisson.h/"', rendered)
            self.assertNotIn('href="poisson.h/"', rendered)

    def test_relative_sandbox_links_rewrite_to_absolute_sandbox_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            sandbox_root.mkdir(parents=True)
            (sandbox_root / 'all-mach.h').write_text('#include "poisson.h"\n', encoding='utf-8')
            (sandbox_root / 'poisson.h').write_text('int poisson(void);\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<a href="poisson.h">Poisson</a>'):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False, generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'sandbox' / 'all-mach.h' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/sandbox/poisson.h/"', rendered)
            self.assertNotIn('href="poisson.h/"', rendered)

    def test_unquoted_relative_source_links_rewrite_to_absolute_source_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'all-mach.h').write_text('#include "run.h"\n', encoding='utf-8')
            (source_root / 'run.h').write_text('void run(void);\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<span class="im">&quot;<a href=./run.h>run.h</a>&quot;</span>'):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'all-mach.h' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/src/run.h/"', rendered)
            self.assertNotIn('href=./run.h', rendered)

    def test_unquoted_relative_sandbox_links_rewrite_to_absolute_sandbox_urls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            sandbox_root.mkdir(parents=True)
            (sandbox_root / 'all-mach.h').write_text('#include "run.h"\n', encoding='utf-8')
            (sandbox_root / 'run.h').write_text('void run(void);\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<span class="im">&quot;<a href=./run.h>run.h</a>&quot;</span>'):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False, generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'sandbox' / 'all-mach.h' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/sandbox/run.h/"', rendered)
            self.assertNotIn('href=./run.h', rendered)

    def test_source_root_relative_tag_links_are_not_rewritten_as_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('FrontPage', '# Front\n')
            (source_root / 'ast').mkdir(parents=True)
            (source_root / 'ast' / 'ast.h').write_text('#include "stack.h"\n', encoding='utf-8')
            (source_root / 'ast' / 'stack.h').write_text('typedef struct Stack Stack;\n', encoding='utf-8')
            with mock.patch('wiki.site.render_darcsit', return_value='<a href=ast/stack.h>stack.h</a><a href=ast/ast.h#Ast>Ast</a>'):
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, artifact_base_url='/artifacts', generate_source_tags=False, jobs=1), output_dir=output).build()
            rendered = (output / 'src' / 'ast' / 'ast.h' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/src/ast/stack.h/"', rendered)
            self.assertIn('href="/src/ast/ast.h/#Ast"', rendered)
            self.assertNotIn('/artifacts/src/ast/ast/stack.h', rendered)
