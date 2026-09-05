"""Export Yue's in-memory book content as a standard, well-formed EPUB.

Yue parses every supported format into the same structure: a list of chapters,
each a list of paragraph strings. Because that structure is uniform, any
single-file format (PDF, TXT, DOCX, HTML, RTF, Markdown) can be re-emitted as a
standard EPUB2 package (mimetype, container.xml, content.opf, toc.ncx and one
XHTML file per chapter).

The module is intentionally dependency-free (stdlib only) so it can run both
from the CLI startup prompt and from anywhere in the reader.
"""

import hashlib
import os
import zipfile
from xml.sax.saxutils import escape

EPUB_MIMETYPE = "application/epub+zip"

# Single-file formats Yue can re-package into a standard EPUB. EPUB itself is
# already in that format, so it is excluded (nothing to convert).
CONVERTIBLE_EXTENSIONS = {".pdf", ".txt", ".docx", ".html", ".rtf", ".md"}

# Common extension that is already a proper EPUB.
EPUB_EXTENSION = ".epub"


def is_epub(file_path):
    """True if the file is already an EPUB (nothing to convert)."""
    return os.path.splitext(file_path)[1].lower() == EPUB_EXTENSION


def is_convertible(file_path):
    """True if the file is a single-file format we can repackage as EPUB."""
    return os.path.splitext(file_path)[1].lower() in CONVERTIBLE_EXTENSIONS


def _chapter_title(chapter):
    """Pick a short human-readable title for a chapter from its first line."""
    for para in chapter:
        text = para.strip()
        if text and len(text) > 2:
            return text[:80]
    return ""


def _chapter_xhtml(chapter, title):
    """Render one chapter as a standalone XHTML document."""
    body_parts = []
    for para in chapter:
        text = para.strip()
        if not text:
            continue
        body_parts.append(f"<p>{escape(text)}</p>")
    body = "\n".join(body_parts) if body_parts else "<p></p>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
        f"<head><title>{escape(title or '')}</title></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _build_ncx(nav_items):
    """Build the EPUB2 NCX navigation document from (id, label) pairs."""
    points = []
    for i, (cid, label) in enumerate(nav_items, start=1):
        points.append(
            f'<navPoint id="nav{i}" playOrder="{i}">'
            f"<navLabel><text>{escape(label)}</text></navLabel>"
            f'<content src="{cid}.xhtml"/></navPoint>'
        )
    nav = "\n".join(points)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "<head><meta name=\"dtb:uid\" content=\"yue-export\"/><meta "
        'name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" '
        'content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
        f"<docTitle><text>{escape(nav_items[0][1]) if nav_items else 'Book'}</text></docTitle>\n"
        f"<navMap>\n{nav}\n</navMap>\n</ncx>\n"
    )


def _build_opf(title, chapter_count):
    """Build the EPUB2 content.opf manifest + spine."""
    manifest = [
        f'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    ]
    spine = []
    for i in range(1, chapter_count + 1):
        cid = f"chapter{i}"
        manifest.append(
            f'<item id="{cid}" href="{cid}.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{cid}"/>')

    escaped_title = escape(title)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">\n'
        "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">\n"
        f'<dc:identifier id="BookId">urn:uuid:yue-{chapter_count}-{hashlib.md5(escaped_title.encode("utf-8")).hexdigest()[:12]}</dc:identifier>\n'
        f"<dc:title>{escaped_title}</dc:title>\n"
        '<dc:language>en</dc:language>\n'
        '<meta name="generator" content="Yue ebook reader"/>\n'
        "</metadata>\n"
        f"<manifest>\n{chr(10).join(manifest)}\n</manifest>\n"
        f"<spine toc=\"ncx\">\n{chr(10).join(spine)}\n</spine>\n"
        "</package>\n"
    )


def build_epub(chapters, title, output_path):
    """Write `chapters` to `output_path` as a standard EPUB2 package.

    Args:
        chapters: list of chapters, each a list of paragraph strings.
        title: book title used in the package metadata.
        output_path: destination path for the .epub file.

    Returns:
        (True, output_path) on success, or (False, reason) on failure.
    """
    if not chapters:
        return False, "No chapters to export (the input may have no readable text)."
    chapters = [c for c in chapters if c]
    if not chapters:
        return False, "No readable content was found, so an EPUB could not be built."

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    nav_items = []
    chapter_files = {}
    for i, chapter in enumerate(chapters, start=1):
        cid = f"chapter{i}"
        label = _chapter_title(chapter) or f"Chapter {i}"
        nav_items.append((cid, label))
        chapter_files[f"{cid}.xhtml"] = _chapter_xhtml(chapter, label)

    opf_path = "OEBPS/content.opf"
    ncx = _build_ncx(nav_items)
    opf = _build_opf(title, len(chapters))
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    )

    try:
        with zipfile.ZipFile(output_path, "w") as zf:
            # Spec requires `mimetype` to be the first entry, stored uncompressed.
            zf.writestr("mimetype", EPUB_MIMETYPE, compress_type=zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", container)
            zf.writestr("OEBPS/toc.ncx", ncx)
            zf.writestr(opf_path, opf)
            for name, data in chapter_files.items():
                zf.writestr(f"OEBPS/{name}", data)
    except OSError as exc:
        return False, f"Could not write EPUB: {exc}"
    except Exception as exc:
        return False, f"Unexpected failure while building EPUB: {exc}"

    return True, output_path


def offer_save_epub(file_path, chapters, console, enabled=True):
    """Offer to save a single-file format as a standard EPUB.

    Called at startup (before the TUI takes over the terminal) so a plain
    yes/no prompt is safe. Informs the user when the format can't be converted
    or when content extraction failed.
    """
    base = os.path.basename(file_path) or file_path
    if is_epub(file_path):
        return
    if not is_convertible(file_path):
        if enabled:
            console.print(
                f"[yellow]Note: '{base}' isn't a single-file format Yue can "
                "repackage as an EPUB.[/yellow]"
            )
        return
    if not enabled:
        return

    if not chapters or not any(chapter for chapter in chapters if chapter):
        console.print(
            f"[yellow]Could not convert '{base}' to EPUB: no readable text was "
            "extracted from the file (it may be an image-based or corrupt "
            "document).[/yellow]"
        )
        return

    default_path = os.path.splitext(file_path)[0] + ".epub"
    console.print(
        f"[cyan]Read '{base}' successfully — this can be saved as a standard "
        "EPUB for other readers/devices.[/cyan]"
    )
    try:
        answer = input(f"Save as a standard EPUB? ({default_path}) [Y/n]: ").strip().lower()
    except (EOFError, OSError):
        answer = ""

    if answer not in ("", "y", "yes"):
        console.print("[dim]Skipped saving the EPUB.[/dim]")
        return

    title = os.path.splitext(os.path.basename(file_path))[0] or "Untitled"
    ok, result = build_epub(chapters, title, default_path)
    if ok:
        console.print(f"[green]Saved standard EPUB: {result}[/green]")
    else:
        console.print(f"[red]{result}[/red]")
