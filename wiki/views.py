from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse

from .darcsit import render as render_darcsit
from .storage import PageNameError, WikiRepository


def repository() -> WikiRepository:
    return WikiRepository(settings.WIKI_ROOT)


def show_page(request, slug: str):
    repo = repository()
    try:
        slug = repo.normalize_slug(slug)
    except PageNameError as exc:
        return HttpResponseBadRequest(str(exc))
    revision = request.GET.get("revision")
    try:
        source = repo.read_page(slug, revision=revision)
    except FileNotFoundError:
        return redirect("edit", slug=slug)
    source_path = None if revision else repo.page_path(slug)
    return render(
        request,
        "wiki/page.html",
        page_context(request, repo, slug)
        | {
            "content_html": render_markdown(source, slug, source_path=source_path),
            "revision": revision,
            "source": source,
        },
    )


def edit_page(request, slug: str):
    repo = repository()
    try:
        slug = repo.normalize_slug(slug)
    except PageNameError as exc:
        return HttpResponseBadRequest(str(exc))
    if request.method == "POST":
        content = request.POST.get("content", "")
        message = request.POST.get("message", "").strip() or f"Update {slug}"
        repo.write_page(slug, content, message)
        return redirect("page", slug=slug)
    try:
        content = repo.read_page(slug)
        creating = False
    except FileNotFoundError:
        content = f"# {title_for(slug)}\n\n"
        creating = True
    return render(
        request,
        "wiki/edit.html",
        page_context(request, repo, slug)
        | {
            "content": content,
            "creating": creating,
        },
    )


def delete_page(request, slug: str):
    repo = repository()
    try:
        slug = repo.normalize_slug(slug)
    except PageNameError as exc:
        return HttpResponseBadRequest(str(exc))
    if request.method == "POST":
        repo.delete_page(slug, request.POST.get("message", "").strip() or f"Delete {slug}")
        return redirect("front")
    if not repo.exists(slug):
        raise Http404("Page not found")
    return render(request, "wiki/delete.html", page_context(request, repo, slug))


def history_page(request, slug: str):
    repo = repository()
    try:
        slug = repo.normalize_slug(slug)
    except PageNameError as exc:
        return HttpResponseBadRequest(str(exc))
    return render(
        request,
        "wiki/history.html",
        page_context(request, repo, slug) | {"revisions": repo.history(slug)},
    )


def raw_page(request, slug: str):
    repo = repository()
    try:
        source = repo.read_page(slug, revision=request.GET.get("revision"))
    except (FileNotFoundError, PageNameError):
        raise Http404("Page not found")
    return HttpResponse(source, content_type="text/plain; charset=utf-8")


def index_page(request):
    repo = repository()
    return render(
        request,
        "wiki/index.html",
        base_context(request, repo) | {"pages": repo.list_pages(), "page_title": "All pages"},
    )


def recent_page(request):
    repo = repository()
    return render(
        request,
        "wiki/recent.html",
        base_context(request, repo)
        | {
            "page_title": "Recent activity",
            "revisions": repo.recent(),
        },
    )


def search_page(request):
    repo = repository()
    query = request.GET.get("q", "").strip()
    return render(
        request,
        "wiki/search.html",
        base_context(request, repo)
        | {
            "page_title": "Search",
            "query": query,
            "results": repo.search(query),
        },
    )


def go_page(request):
    repo = repository()
    try:
        slug = repo.normalize_slug(request.GET.get("page"))
    except PageNameError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("page", slug=slug)


def render_markdown(source: str, slug: str = "", source_path=None) -> str:
    return render_darcsit(source, slug, source_path=source_path)


def page_context(request, repo: WikiRepository, slug: str) -> dict:
    return base_context(request, repo) | {
        "page_slug": slug,
        "page_title": title_for(slug),
        "page_exists": repo.exists(slug),
    }


def base_context(request, repo: WikiRepository) -> dict:
    return {
        "wiki_title": settings.WIKI_TITLE,
        "front_url": reverse("front"),
        "all_pages_url": reverse("index"),
        "recent_url": reverse("recent"),
        "search_url": reverse("search"),
        "go_url": reverse("go"),
        "mathjax_url": settings.MATHJAX_URL,
    }


def title_for(slug: str) -> str:
    return slug.rsplit("/", 1)[-1].replace("_", " ")
