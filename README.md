# GititPy

A rudimentary Django clone of Gitit. Pages are server-rendered Markdown files
stored in a local Git repository.

## Development

```bash
direnv allow
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

## Features

- View, create, edit, and delete Markdown pages.
- Store pages as files under `wiki-pages/`.
- Commit every change to Git.
- Show page history and recent activity.
- List all pages and run simple full-text search.
- Use Gitit static assets for the basic layout.
- Render Markdown through Pandoc when available.
- Render source-code pages such as `example.c` or `script.py`, including a
  small Darcsit-style page-magic pass for documentation blocks.

This first pass intentionally has no login system.
