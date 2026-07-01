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

class StaticSiteTests(unittest.TestCase):

    def test_static_build_renders_pages_and_assets_without_source_when_no_source_tree_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            repo = WikiRepository(wiki_root)
            repo.write_page('Front Page', '# Static Front\n\nSee [[Help]].')
            repo.write_page('sandbox', '# Sandbox page\n')
            repo.write_page('sandbox/PageOne', '# Page One\n')
            (wiki_root / 'sandbox' / 'movie.mp4').write_bytes(b'movie')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root), output_dir=output)
            builder.build()
            self.assertTrue((output / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'PageOne' / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'movie.mp4').is_file())
            self.assertFalse((output / 'src' / 'index.html').exists())
            self.assertTrue((output / 'static' / 'wiki' / 'css' / 'gititpy.css').is_file())
            self.assertTrue((output / 'static' / 'wiki' / 'js' / 'search.js').is_file())
            self.assertTrue((output / 'search-index.json').is_file())
            self.assertIn('Static Front', (output / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('href="/sandbox/"', (output / '_index.html').read_text(encoding='utf-8'))

    def test_static_build_renders_default_source_tree_in_parallel(self):
        source = '/**\n        # Source file\n\n        Rendered statically.\n         */\n        int main(void) {\n            return 0;\n        }\n        '
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n\n[Source](/src/example.c)\n')
            source_root.mkdir(parents=True)
            (source_root / 'example.c').write_text(source, encoding='utf-8')
            (source_root / 'README.md').write_text('[Example](example.c)\n', encoding='utf-8')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=2), output_dir=output)
            builder.build()
            self.assertTrue((output / 'src' / 'index.html').is_file())
            self.assertTrue((output / 'src' / 'example.c' / 'index.html').is_file())
            self.assertFalse((output / 'src' / 'example.c.html').exists())
            self.assertIn('Rendered statically.', (output / 'src' / 'example.c' / 'index.html').read_text(encoding='utf-8'))
            source_page = (output / 'src' / 'example.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('aria-label="breadcrumb"', source_page)
            self.assertIn('<li class="breadcrumb-item"><a href="/src/">src</a></li>', source_page)
            self.assertIn('<li class="breadcrumb-item active" aria-current="page">example.c</li>', source_page)
            self.assertNotIn('<h1 class="pageTitle">/src/example.c</h1>', source_page)
            self.assertIn('href="/src/example.c/"', (output / 'src' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('bi-file-earmark-text', (output / 'src' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('href="/src/example.c/"', (output / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('href="/src/example.c/"', (output / 'src' / 'README.md' / 'index.html').read_text(encoding='utf-8'))

    def test_static_build_pretty_prints_wiki_code_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Example.c', '/**\n# Example\n\nRendered as source.\n*/\nint main(void) { return 0; }\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            rendered = (output / 'Example.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('Rendered as source.', rendered)
            self.assertIn('sourceCode', rendered)

    def test_static_build_treats_legacy_frontpage_file_as_root_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            wiki_root.mkdir()
            (wiki_root / 'FrontPage.md').write_text('# Legacy Front\n', encoding='utf-8')

            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()

            self.assertIn('Legacy Front', (output / 'index.html').read_text(encoding='utf-8'))
            self.assertFalse((output / 'FrontPage' / 'index.html').exists())
            self.assertFalse((output / 'Front_Page' / 'index.html').exists())

    def test_static_build_renders_build_scripts_as_whole_file_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20)\nproject(example)\n', encoding='utf-8')
            (source_root / 'dotest').write_text('#!/usr/bin/env bash\n/**\n# Not literate prose\n*/\necho ok\n', encoding='utf-8')

            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, source_root=source_root, generate_source_tags=False, jobs=1), output_dir=output).build()

            cmake = (output / 'src' / 'CMakeLists.txt' / 'index.html').read_text(encoding='utf-8')
            script = (output / 'src' / 'dotest' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('cmake_minimum_required', cmake)
            self.assertIn('sourceCode cmake', cmake)
            self.assertIn('echo', script)
            self.assertIn('ok', script)
            self.assertIn('sourceCode bash', script)
            self.assertNotIn('<h1 id="not-literate-prose">Not literate prose</h1>', script)

    def test_static_build_passes_base_url_to_darcsit_tag_link_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'example.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')

            with mock.patch('wiki.site.render_darcsit', return_value='<p>Rendered</p>') as render:
                StaticSiteBuilder(
                    config=SiteConfig(
                        base_dir=root,
                        wiki_root=wiki_root,
                        source_root=source_root,
                        generate_source_tags=False,
                        jobs=1,
                    ),
                    output_dir=output,
                    base_url='/wiki',
                ).build()

            source_calls = [call for call in render.call_args_list if call.kwargs.get('source_path') == source_root / 'example.c']
            self.assertEqual(len(source_calls), 1)
            self.assertEqual(source_calls[0].kwargs.get('basilisk_url'), '/wiki')

    def test_static_build_writes_sitemap_and_robots_txt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            WikiRepository(wiki_root).write_page('Guide', '# Guide\n')
            WikiRepository(wiki_root).write_page('docs/Page', '# Docs\n')
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page('README', '# Sandbox\n')
            (source_root / 'sub').mkdir(parents=True)
            (source_root / 'sub' / 'example.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            (source_root / 'sub' / 'example.c.tags').write_text('decl main sub/example.c 1\n', encoding='utf-8')
            (source_root / 'blob.bin').write_bytes(b'\x00\x01')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, source_root=source_root, generate_source_tags=False, jobs=1), output_dir=output, base_url='https://example.org/docs').build()
            sitemap = (output / 'sitemap.xml').read_text(encoding='utf-8')
            self.assertIn('<loc>https://example.org/docs/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/Guide/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/docs/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/docs/Page/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/sandbox/README.md/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/src/sub/</loc>', sitemap)
            self.assertIn('<loc>https://example.org/docs/src/sub/example.c/</loc>', sitemap)
            self.assertNotIn('example.c.tags', sitemap)
            self.assertNotIn('blob.bin', sitemap)
            self.assertNotIn('_search.html', sitemap)
            self.assertNotIn('_history', sitemap)
            self.assertNotIn('_raw', sitemap)
            robots = (output / 'robots.txt').read_text(encoding='utf-8')
            self.assertIn('Allow: /docs/', robots)
            self.assertIn('Disallow: /docs/_search.html', robots)
            self.assertIn('Sitemap: https://example.org/docs/sitemap.xml', robots)
            self.assertFalse((output / '_recent.html').exists())
            self.assertFalse((output / '_history').exists())

    def test_static_build_writes_not_found_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')

            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output, base_url='https://example.org/docs').build()

            rendered = (output / '404.html').read_text(encoding='utf-8')
            self.assertIn('<title>GititPy - Page not found</title>', rendered)
            self.assertIn('<meta name="robots" content="noindex">', rendered)
            self.assertNotIn('rel="canonical"', rendered)
            self.assertNotIn('id="sidebar"', rendered)
            self.assertNotIn('border rounded', rendered)
            self.assertIn('href="https://example.org/docs/"', rendered)
            self.assertIn('href="https://example.org/docs/_search.html"', rendered)
            self.assertNotIn('404.html', (output / 'sitemap.xml').read_text(encoding='utf-8'))

    def test_static_build_writes_canonical_links_for_indexable_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            WikiRepository(wiki_root).write_page('Guide', '# Guide\n')
            WikiRepository(wiki_root).write_page('docs/Page', '# Docs\n')
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page('README', '# Sandbox\n')
            (source_root / 'sub').mkdir(parents=True)
            (source_root / 'sub' / 'example.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, source_root=source_root, generate_source_tags=False, jobs=1), output_dir=output, base_url='https://example.org/docs').build()
            self.assertIn('<link rel="canonical" href="https://example.org/docs/">', (output / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('<link rel="canonical" href="https://example.org/docs/Guide/">', (output / 'Guide' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('<link rel="canonical" href="https://example.org/docs/docs/">', (output / 'docs' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('<link rel="canonical" href="https://example.org/docs/sandbox/README.md/">', (output / 'sandbox' / 'README.md' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('<link rel="canonical" href="https://example.org/docs/src/sub/example.c/">', (output / 'src' / 'sub' / 'example.c' / 'index.html').read_text(encoding='utf-8'))
            self.assertNotIn('rel="canonical"', (output / '_search.html').read_text(encoding='utf-8'))
            self.assertFalse((output / '_history' / 'Guide.html').exists())

    def test_static_build_writes_enhanced_search_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')

            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()

            rendered = (output / '_search.html').read_text(encoding='utf-8')
            self.assertIn('type="search"', rendered)
            self.assertIn('id="search-status"', rendered)
            self.assertIn('window.GititPySearchIndex = "/search-index.json"', rendered)

    def test_static_build_links_wiki_pages_to_github_editor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            repo = WikiRepository(wiki_root)
            repo.write_page('Front Page', '# Front\n')
            repo.write_page('Guides/Intro Page', '# Intro\n')

            StaticSiteBuilder(
                config=SiteConfig(
                    base_dir=root,
                    wiki_root=wiki_root,
                    build_source=False,
                    edit_base_url='https://github.com/example/wiki/edit/main',
                ),
                output_dir=output,
            ).build()

            front = (output / 'index.html').read_text(encoding='utf-8')
            guide = (output / 'Guides' / 'Intro_Page' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="https://github.com/example/wiki/edit/main/Front%20Page.md"', front)
            self.assertIn('href="https://github.com/example/wiki/edit/main/Guides/Intro_Page.md"', guide)

    def test_static_build_can_skip_default_source_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'example.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output)
            builder.build()
            self.assertFalse((output / 'src' / 'index.html').exists())

    def test_static_build_renders_sandbox_from_separate_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            WikiRepository(wiki_root).write_page('sandbox/OldPage', '# Old Sandbox\n')
            sandbox_repo = WikiRepository(sandbox_root, seed_defaults=False)
            sandbox_repo.write_page('README', '# New Sandbox\n\nsearchable sandbox text')
            sandbox_repo.write_page('user/README', '# User Sandbox\n')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False), output_dir=output)
            builder.build()
            self.assertTrue((output / 'sandbox' / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'README.md' / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'user' / 'index.html').is_file())
            self.assertTrue((output / 'sandbox' / 'user' / 'README.md' / 'index.html').is_file())
            self.assertFalse((output / 'sandbox' / 'OldPage.html').exists())
            self.assertFalse((sandbox_root / 'Front Page.md').exists())
            self.assertFalse((sandbox_root / 'Help.md').exists())
            sandbox_page = (output / 'sandbox' / 'README.md' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('New Sandbox', sandbox_page)
            self.assertIn('aria-label="breadcrumb"', sandbox_page)
            self.assertIn('<li class="breadcrumb-item"><a href="/sandbox/">sandbox</a></li>', sandbox_page)
            self.assertIn('<li class="breadcrumb-item active" aria-current="page">README.md</li>', sandbox_page)
            self.assertNotIn('<h1 class="pageTitle">/sandbox/README.md</h1>', sandbox_page)
            self.assertIn('href="/sandbox/user/"', (output / 'sandbox' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('bi-folder', (output / 'sandbox' / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('href="/sandbox/"', (output / 'sandbox' / 'user' / 'index.html').read_text(encoding='utf-8'))
            search_index = (output / 'search-index.json').read_text(encoding='utf-8')
            self.assertIn('sandbox/README', search_index)
            self.assertIn('/sandbox/README.md/', search_index)

    def test_static_sidebar_links_to_sandbox_when_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            WikiRepository(sandbox_root, seed_defaults=False).write_page('README', '# Sandbox\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False), output_dir=output).build()
            rendered = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="/sandbox/"', rendered)
            self.assertIn('>Sandbox</a>', rendered)

    def test_incremental_build_skips_unchanged_page_renders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Incremental Front\n')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output)
            builder.build()
            with mock.patch('wiki.site.render_darcsit', side_effect=AssertionError('should skip')):
                result = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            self.assertGreater(result.skipped_files, 0)
            self.assertTrue((output / '.gititpy-build.json').is_file())

    def test_incremental_build_uses_content_hash_not_mtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            repo = WikiRepository(wiki_root)
            repo.write_page('Front Page', '# Hash Front\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            source_path = repo.page_path('Front Page')
            stat_result = source_path.stat()
            os.utime(source_path, ns=(stat_result.st_atime_ns + 1000000000, stat_result.st_mtime_ns + 1000000000))
            with mock.patch('wiki.site.render_darcsit', side_effect=AssertionError('should skip')):
                result = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            manifest = json.loads((output / '.gititpy-build.json').read_text(encoding='utf-8'))
            item = manifest['items']['wiki:Front Page']
            self.assertGreater(result.skipped_files, 0)
            self.assertIn('sha256', item)
            self.assertNotIn('mtime_ns', item)

    def test_incremental_build_rerenders_changed_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            repo = WikiRepository(wiki_root)
            repo.write_page('Front Page', '# Incremental Front\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            repo.write_page('Front Page', '# Incremental Front Changed\n\nExtra text.\n')
            with mock.patch('wiki.site.render_darcsit', return_value='<p>Changed render</p>') as render:
                StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            self.assertEqual(render.call_count, 1)
            self.assertIn('Changed render', (output / 'index.html').read_text(encoding='utf-8'))

    def test_force_rebuild_ignores_incremental_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Incremental Front\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build()
            with mock.patch('wiki.site.render_darcsit', return_value='<p>Forced render</p>') as render:
                result = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, build_source=False), output_dir=output).build(clean=True, force_rebuild=True)
            self.assertGreaterEqual(render.call_count, 1)
            self.assertEqual(result.skipped_files, 0)
            self.assertIn('Forced render', (output / 'index.html').read_text(encoding='utf-8'))

    def test_source_render_failure_falls_back_to_plain_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            source_root.mkdir(parents=True)
            (source_root / 'generated.c').write_text('int generated(void) { return 1; }\n', encoding='utf-8')

            def render_or_fail(source, slug='', source_path=None, table_of_contents=True, basilisk_root=None, basilisk_url=None):
                if slug == 'generated.c':
                    raise RuntimeError('Darcsit literate-c failed for generated.c.')
                return '<p>Rendered page</p>'
            with mock.patch('wiki.site.render_darcsit', side_effect=render_or_fail):
                builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, jobs=1), output_dir=output)
                result = builder.build()
            rendered = (output / 'src' / 'generated.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('generated', rendered)
            self.assertTrue(any('plain code' in warning for warning in result.warnings), result.warnings)

    def test_sandbox_render_failure_falls_back_to_plain_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            sandbox_root.mkdir(parents=True)
            (sandbox_root / 'generated.c').write_text('int generated(void) { return 1; }\n', encoding='utf-8')

            def render_or_fail(source, slug='', source_path=None, table_of_contents=True, basilisk_root=None, basilisk_url=None):
                if slug == 'generated.c':
                    raise RuntimeError('Darcsit literate-c failed for generated.c.')
                return '<p>Rendered page</p>'
            with mock.patch('wiki.site.render_darcsit', side_effect=render_or_fail):
                result = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False, jobs=1), output_dir=output).build()
            rendered = (output / 'sandbox' / 'generated.c' / 'index.html').read_text(encoding='utf-8')
            self.assertIn('generated', rendered)
            self.assertTrue(any('plain code' in warning for warning in result.warnings), result.warnings)

    def test_static_builder_honors_config_base_url_and_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'configured-public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, output_dir=output, base_url='/docs', build_source=False)).build()
            self.assertTrue((output / 'index.html').is_file())
            self.assertIn('<link rel="canonical" href="/docs/">', (output / 'index.html').read_text(encoding='utf-8'))

    def test_static_builder_constructor_overrides_config_base_url_and_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            configured_output = root / 'configured-public'
            override_output = root / 'override-public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, output_dir=configured_output, base_url='/docs', build_source=False), output_dir=override_output, base_url='/override').build()
            self.assertFalse((configured_output / 'index.html').exists())
            self.assertTrue((override_output / 'index.html').is_file())
            self.assertIn('<link rel="canonical" href="/override/">', (override_output / 'index.html').read_text(encoding='utf-8'))

    def test_static_builder_missing_explicit_sandbox_root_warns_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            sandbox_root = root / 'missing-sandbox'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            result = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, sandbox_root=sandbox_root, build_source=False), output_dir=output).build()
            self.assertFalse(sandbox_root.exists())
            self.assertFalse((output / 'sandbox' / 'index.html').exists())
            self.assertIn('Configured sandbox tree does not exist', result.warnings[0])

    def test_source_browser_removes_stale_directory_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            source_root = root / 'basilisk' / 'src'
            output = root / 'public'
            WikiRepository(wiki_root).write_page('Front Page', '# Front\n')
            (source_root / 'old').mkdir(parents=True)
            (source_root / 'old' / 'example.c').write_text('int main(void) { return 0; }\n', encoding='utf-8')
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, generate_source_tags=False, jobs=1), output_dir=output).build()
            self.assertTrue((output / 'src' / 'old' / 'index.html').is_file())
            (source_root / 'old' / 'example.c').unlink()
            (source_root / 'old').rmdir()
            StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, generate_source_tags=False, jobs=1), output_dir=output).build()
            self.assertFalse((output / 'src' / 'old' / 'index.html').exists())

    def test_static_build_uses_site_template_and_static_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wiki_root = root / 'pages'
            output = root / 'public'
            template_root = root / 'templates'
            static_root = root / 'static'
            WikiRepository(wiki_root).write_page('Front Page', '# Template Front\n')
            (template_root / 'wiki').mkdir(parents=True)
            (template_root / 'wiki' / 'page.html').write_text('<!doctype html><title>{{ wiki_title }}</title><main>{{ content_html|safe }}</main>', encoding='utf-8')
            (static_root / 'wiki' / 'css').mkdir(parents=True)
            (static_root / 'wiki' / 'css' / 'custom.css').write_text('body { color: rgb(1, 2, 3); }', encoding='utf-8')
            builder = StaticSiteBuilder(config=SiteConfig(base_dir=root, wiki_root=wiki_root, template_roots=(template_root,), static_roots=(static_root,)), output_dir=output)
            builder.build()
            rendered = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('<main>', rendered)
            self.assertIn('Template Front', rendered)
            self.assertEqual((output / 'static' / 'wiki' / 'css' / 'custom.css').read_text(encoding='utf-8'), 'body { color: rgb(1, 2, 3); }')
