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

## Site configuration

`gititpy` reads `gititpy.toml` from the project root when it exists. Command
line options override the file, so local experiments do not require editing the
checked-in config.

```toml
[site]
title = "My Site"
base_url = ""

[paths]
wiki_root = "wiki-pages"
source_root = "basilisk/src"
output = "public"
template_roots = ["templates"]
static_roots = ["static"]

[build]
source = true
jobs = 4
```

A consuming site can keep content and customization in this shape:

```text
wiki-pages/       Markdown, source pages, images, movies, and other page assets
basilisk/src/     Optional tree rendered under /src/
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

## Features

- Store pages as plain files under `wiki-pages/`.
- Render Markdown through Pandoc.
- Render source-code pages such as `example.c` or `script.py`, including a
  Darcsit-style page-magic pass for documentation blocks.
- Render `.bib` files and bibliography blocks with the Python bibliography
  renderer.
- Build static page output, raw source copies, directory indexes, optional
  `/src/` source pages, and a client-side search index.
- Optionally show page history and recent activity if `wiki-pages/` itself is a
  Git repository; no Git repository is created or modified by default.
- Use Gitit static assets for the basic layout.

There is intentionally no login system in the static-generator workflow.
