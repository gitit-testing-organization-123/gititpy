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
