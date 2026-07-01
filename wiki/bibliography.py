import html
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from pylatexenc.latex2text import LatexNodes2Text


BIB_START_RE = re.compile(r"^\s*~~~bib\s*$")
BIB_END_RE = re.compile(r"^\s*~~~\s*$")
LATEX_TO_TEXT = LatexNodes2Text()


@dataclass
class BibEntry:
    kind: str
    key: str
    fields: dict[str, str]


def replace_bibliography_blocks(source: str) -> tuple[str, bool]:
    lines = source.splitlines(keepends=True)
    rendered: list[str] = []
    replaced = False
    i = 0

    while i < len(lines):
        if not BIB_START_RE.match(lines[i].strip()):
            rendered.append(lines[i])
            i += 1
            continue

        start = i
        i += 1
        body: list[str] = []
        while i < len(lines) and not BIB_END_RE.match(lines[i].strip()):
            body.append(lines[i])
            i += 1

        if i == len(lines):
            rendered.extend(lines[start:])
            break

        i += 1
        block_line_count = i - start
        rendered.append(render_bibliography_html("".join(body)) + "\n")
        rendered.extend("\n" for _ in range(block_line_count - 1))
        replaced = True

    return "".join(rendered), replaced


def render_bibliography_html(source: str) -> str:
    entries = parse_bibliography(source)
    if not entries:
        return '<div class="bibtex"><p>No bibliography entries.</p></div>'
    rows = "".join(render_entry(entry) for entry in entries)
    return f'<div class="bibtex"><table>{rows}</table></div>'


def parse_bibliography(source: str, fetch_hal: bool = True) -> list[BibEntry]:
    entries: list[BibEntry] = []
    for kind, key, body in raw_entries(source):
        if kind.lower() == "hal":
            entries.extend(parse_hal_entry(key, body, fetch_hal))
            continue
        entries.append(BibEntry(kind=kind, key=key, fields=parse_fields(body)))
    return entries


def parse_hal_entry(key: str, body: str, fetch_hal: bool) -> list[BibEntry]:
    hal_id = clean_tex(body).strip().strip(",")
    if not fetch_hal:
        return [missing_hal_entry(key, hal_id)]
    try:
        entries = parse_bibliography(fetch_hal_bibtex(hal_id), fetch_hal=False)
    except URLError:
        return [missing_hal_entry(key, hal_id)]
    if not entries:
        return [missing_hal_entry(key, hal_id)]
    entries[0].key = key
    return entries


def fetch_hal_bibtex(hal_id: str) -> str:
    url = (
        "https://api.archives-ouvertes.fr/search/"
        f"?q=halId_s:{quote(hal_id)}&wt=bibtex&rows=1"
    )
    with urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def missing_hal_entry(key: str, hal_id: str) -> BibEntry:
    return BibEntry("misc", key, {"title": f"{hal_id} not found or server error"})


def raw_entries(source: str):
    i = 0
    while True:
        start = source.find("@", i)
        if start == -1:
            return
        match = re.match(r"@([A-Za-z]+)\s*\{", source[start:])
        if not match:
            i = start + 1
            continue
        kind = match.group(1)
        body_start = start + match.end()
        body_end = find_entry_end(source, body_start)
        if body_end is None:
            return
        body = source[body_start:body_end]
        key, fields = split_key(body)
        if key:
            yield kind, key, fields
        i = body_end + 1


def find_entry_end(source: str, start: int) -> int | None:
    depth = 1
    i = start
    while i < len(source):
        char = source[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def split_key(body: str) -> tuple[str, str]:
    depth = 0
    in_quote = False
    for i, char in enumerate(body):
        if char == "\\":
            continue
        if char == '"':
            in_quote = not in_quote
        elif not in_quote and char == "{":
            depth += 1
        elif not in_quote and char == "}":
            depth -= 1
        elif not in_quote and depth == 0 and char == ",":
            return body[:i].strip(), body[i + 1 :].strip()
    return body.strip(), ""


def parse_fields(source: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i = 0
    while i < len(source):
        while i < len(source) and source[i] in " \t\r\n,":
            i += 1
        name_start = i
        while i < len(source) and re.match(r"[A-Za-z0-9_-]", source[i]):
            i += 1
        name = source[name_start:i].lower()
        while i < len(source) and source[i].isspace():
            i += 1
        if not name or i >= len(source) or source[i] != "=":
            break
        i += 1
        while i < len(source) and source[i].isspace():
            i += 1
        value, i = read_value(source, i)
        fields[name] = clean_tex(value)
    return fields


def read_value(source: str, i: int) -> tuple[str, int]:
    if i >= len(source):
        return "", i
    if source[i] == "{":
        return read_braced_value(source, i)
    if source[i] == '"':
        return read_quoted_value(source, i)
    start = i
    while i < len(source) and source[i] != ",":
        i += 1
    return source[start:i].strip(), i


def read_braced_value(source: str, i: int) -> tuple[str, int]:
    depth = 1
    start = i + 1
    i += 1
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i], i + 1
        i += 1
    return source[start:], i


def read_quoted_value(source: str, i: int) -> tuple[str, int]:
    start = i + 1
    i += 1
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == '"':
            return source[start:i], i + 1
        i += 1
    return source[start:], i


def render_entry(entry: BibEntry) -> str:
    body = html.escape(format_entry(entry))
    key = html.escape(entry.key, quote=True)
    return (
        '<tr valign="top">'
        '<td align="right" class="bibtexnumber">'
        f'[<a name="{key}">{key}</a>]'
        "</td>"
        f'<td class="bibtexitem"><p>{body}</p></td>'
        "</tr>"
    )


def format_entry(entry: BibEntry) -> str:
    fields = entry.fields
    parts = []
    authors = format_people(fields.get("author") or fields.get("editor") or "")
    if authors:
        parts.append(authors)
    if fields.get("title"):
        parts.append(fields["title"])
    venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher")
    if venue:
        parts.append(venue)
    if fields.get("year"):
        parts.append(fields["year"])
    text = ". ".join(part.rstrip(".") for part in parts if part)
    links = []
    if fields.get("doi"):
        doi = fields["doi"]
        links.append(f"doi:{doi}")
    if fields.get("url"):
        links.append(fields["url"])
    if links:
        text = f"{text}. {' '.join(links)}" if text else " ".join(links)
    return text or entry.key


def format_people(source: str) -> str:
    people = [format_person(part.strip()) for part in re.split(r"\s+and\s+", source) if part.strip()]
    if len(people) <= 1:
        return "".join(people)
    return ", ".join(people[:-1]) + f" and {people[-1]}"


def format_person(name: str) -> str:
    parts = [part.strip() for part in name.split(",")]
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}".strip()
    if len(parts) == 3:
        return f"{parts[2]} {parts[1]} {parts[0]}".strip()
    return name


def clean_tex(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return LATEX_TO_TEXT.latex_to_text(value)
