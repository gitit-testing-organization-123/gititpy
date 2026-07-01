# GititPy

A rudimentary Gitit/Darcsit-style static site generator. Pages are Markdown or
source files stored in the project tree and rendered into publishable HTML.

## Development

```bash
direnv allow
python -m gititpy serve --output public
```

Then open <http://127.0.0.1:8000/>.

## Static build

```bash
python -m gititpy build --output public
```

For a GitHub Pages project site, pass the repository URL prefix:

```bash
python -m gititpy build --output public --base-url /repository-name
```

External artifacts can be linked with deterministic `/artifacts/...` paths and
rewritten to a separate artifact host:

```bash
python -m gititpy build --artifacts-base-url https://artifacts.example.org
```

Then `/artifacts/examples/bubble/movie.mp4` renders as
`https://artifacts.example.org/examples/bubble/movie.mp4`. Upload those files
separately with a tool such as `rclone`; `gititpy` only rewrites the links.

If `basilisk/src` exists under the project root, it is rendered under `/src/`
automatically. You can override or disable this:

```bash
python -m gititpy build --output public --source-root basilisk/src
python -m gititpy build --output public --no-source
```

Source rendering is parallelized with a conservative default of up to four jobs.
Adjust it with:

```bash
python -m gititpy build --output public --jobs 8
```

For C source pages, `gititpy` can run `qcc -tags` before rendering so the
Darcsit `codeblock` helper can annotate declarations, calls, and includes. This
is enabled by default for `.c` and `.h` files. Disable it or use a specific
`qcc` binary with:

```bash
python -m gititpy build --no-source-tags
python -m gititpy build --qcc-command /path/to/qcc
```

For a noisier local build, pass `--verbose` or set `verbose = true` under
`[build]` in `gititpy.toml`.

```bash
python -m gititpy build --verbose
```

Builds are incremental by default. `gititpy` writes
`public/.gititpy-build.json` and skips page/source renders and file copies when
the input file content hash, input size, renderer config, and expected outputs
still match. Global indexes and directory pages are still regenerated because
they are cheap and depend on the full tree. This makes restored `public/`
directories useful in CI caches, where fresh checkouts often have different
file modification times.

To ignore the manifest and rebuild from scratch:

```bash
python -m gititpy build --force-rebuild
```

Tables of contents are generated with Pandoc by default. Disable them globally
with `table_of_contents = false` or `--no-toc`, or per page with metadata:

```markdown
---
toc: no
...
```

To test the packaged application through Nix, run:

```bash
nix build
```

Nix only streams derivation logs when requested:

```bash
nix build -L
```

## Site configuration

`gititpy` reads `gititpy.toml` from the project root when it exists. Command
line options override the file, so local experiments do not require editing the
checked-in config.

```toml
[site]
title = "My Site"
base_url = ""
table_of_contents = true

[paths]
wiki_root = "wiki-pages"
sandbox_root = "sandbox"
source_root = "basilisk/src"
output = "public"
template_roots = ["templates"]
static_roots = ["static"]

[artifacts]
base_url = "https://artifacts.example.org"

[build]
source = true
source_tags = true
qcc_command = "qcc"
jobs = 4
verbose = false
```

A consuming site can keep content and customization in this shape:

```text
wiki-pages/       Markdown, source pages, images, movies, and other page assets
sandbox/          Optional sandbox page tree rendered under /sandbox/
basilisk/src/     Optional tree rendered under /src/
artifacts/        Optional local staging tree for separately-uploaded artifacts
templates/        Optional Jinja template overrides, e.g. templates/wiki/base.html
static/           Optional static overrides copied to /static/
public/           Generated output
```

For GitHub Pages, the included `.github/workflows/pages.yml` checks out the
repo, installs Pandoc and the local package, runs `gititpy build`, and deploys
`public/`. Expensive plot or movie generation should happen before this build
and commit or otherwise provide the resulting artifacts to the site tree.

The build copies static assets, renders wiki pages, renders directory indexes,
copies non-rendered assets such as images or movies, and writes
`search-index.json` for client-side search. It does not run long simulations or
generate plot/movie artifacts; those should be produced separately and stored
alongside the pages or served from external artifact storage.

Inline `~~~gnuplot` and `~~~pythonplot` blocks can be generated after the test
suite has produced its data files. The artifact tool ports Basilisk's AWK
extraction logic to Python, writes `plots` and `plots.py` inside each artifact
directory, and executes them from there.
Plot generation only runs for `name.c` when the corresponding `name/` artifact
directory already exists, so sources without test output directories are
skipped.

```bash
python -m gititpy.artifacts_cli \
  --source-root basilisk/src \
  --artifact-root basilisk/build/release/src \
  plots list

python -m gititpy.artifacts_cli \
  --source-root basilisk/src \
  --artifact-root basilisk/build/release/src \
  plots generate

python -m gititpy.artifacts_cli \
  --source-root basilisk/src \
  --artifact-root basilisk/build/release/src \
  stage --dest public/artifacts
```

## Features

- Store pages as plain files under `wiki-pages/`.
- Render Markdown through Pandoc.
- Render source-code pages such as `example.c` or `script.py`, including a
  Darcsit-style page-magic pass for documentation blocks.
- Render `.bib` files and bibliography blocks with the Python bibliography
  renderer.
- Build static page output, raw source copies, directory indexes, optional
  `/src/` source pages, and a client-side search index.
- Use Gitit static assets for the basic layout.

There is intentionally no login system in the static-generator workflow.
