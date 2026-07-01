from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


class TemplateRenderer:
    def __init__(self, override_roots: tuple[Path, ...] = ()):
        template_root = Path(__file__).resolve().parent / "templates"
        roots = [str(root) for root in override_roots if root.exists()]
        roots.append(str(template_root))
        self.environment = Environment(
            loader=FileSystemLoader(roots),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, template: str, context: dict) -> str:
        return self.environment.get_template(template).render(**context)
