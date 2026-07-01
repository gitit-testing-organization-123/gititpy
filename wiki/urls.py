from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit


class StaticUrls:
    def __init__(self, base_url: str = ""):
        self.base_url = self.normalize_base_url(base_url)

    def normalize_base_url(self, base_url: str) -> str:
        if not base_url or base_url == "/":
            return ""
        parsed = urlsplit(base_url)
        if parsed.scheme and parsed.netloc:
            return base_url.rstrip("/")
        return f"/{base_url.strip('/')}"

    def front_url(self) -> str:
        return self.url("/")

    def index_url(self) -> str:
        return self.url("/_index.html")

    def search_url(self) -> str:
        return self.url("/_search.html")

    def search_index_url(self) -> str:
        return self.url("/search-index.json")

    def sitemap_url(self) -> str:
        return self.url("/sitemap.xml")

    def static_url(self) -> str:
        return self.url("/static/")

    def page_url(self, slug: str) -> str:
        if slug == "FrontPage":
            return self.front_url()
        return self.tree_page_url(None, slug)

    def wiki_asset_url(self, repo, slug: str) -> str:
        return self.url(f"/{repo.page_filename(slug)}")

    def directory_url(self, slug: str) -> str:
        if not slug:
            return self.index_url()
        return self.tree_directory_url(None, slug)

    def page_output_path(self, output_dir: Path, slug: str) -> Path:
        if slug == "FrontPage":
            return output_dir / "index.html"
        return self.tree_page_output_path(output_dir, None, slug)

    def directory_output_path(self, output_dir: Path, slug: str) -> Path:
        if not slug:
            return output_dir / "_directory.html"
        return self.tree_directory_output_path(output_dir, None, slug)

    def tree_directory_url(self, prefix: str | None, path: str) -> str:
        base = self.tree_url_path(prefix, path)
        if not base.endswith("/"):
            base = f"{base}/"
        return self.url(base)

    def tree_page_url(self, prefix: str | None, path: str) -> str:
        base = self.tree_url_path(prefix, path)
        if not base.endswith("/"):
            base = f"{base}/"
        return self.url(base)

    def tree_asset_url(self, prefix: str | None, path: str) -> str:
        return self.url(self.tree_url_path(prefix, path))

    def tree_url_path(self, prefix: str | None, path: str) -> str:
        parts = []
        if prefix:
            parts.append(prefix.strip("/"))
        if path:
            parts.append(path.strip("/"))
        if not parts:
            return "/"
        return f"/{'/'.join(parts)}"

    def tree_directory_output_path(self, output_dir: Path, prefix: str | None, path: str) -> Path:
        if not prefix and not path:
            return output_dir / "_directory.html"
        return self.tree_output_base(output_dir, prefix, path) / "index.html"

    def tree_page_output_path(self, output_dir: Path, prefix: str | None, path: str) -> Path:
        return self.tree_output_base(output_dir, prefix, path) / "index.html"

    def tree_asset_output_path(self, output_dir: Path, prefix: str | None, path: str) -> Path:
        return self.tree_output_base(output_dir, prefix, "") / self.local_path(path)

    def tree_output_base(self, output_dir: Path, prefix: str | None, path: str) -> Path:
        base = output_dir
        if prefix:
            base = base / self.local_path(prefix.strip("/"))
        if path:
            base = base / self.local_path(path.strip("/"))
        return base

    def url(self, path: str) -> str:
        if path == "/":
            return f"{self.base_url}/" if self.base_url else "/"
        return f"{self.base_url}{quote(path, safe='/._-~')}"

    def robots_path(self, path: str) -> str:
        parsed = urlsplit(self.url(path))
        return parsed.path or "/"

    def local_path(self, path: str) -> Path:
        return Path(PurePosixPath(path))
