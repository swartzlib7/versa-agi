"""What `agictl message send` may attach, checked before anything is sent.

State: design/spec/state/state_message_body.md (rules 9 to 13). No click, no network.

MEDIA_TYPES is what the VersaVoice AI server's `/attachments/upload` accepts
(`handleUploadMedia`, app/functions/src/index.ts), which is what the app itself can send
(`extensionMimeMap`, app/lib/features/common/data/media_upload_service.dart). Change the server
and the app first, then this list and the table in skills/communication_basic.md and
skills/communication.md.
"""

from __future__ import annotations

import os

MAX_ATTACHMENTS = 10
MEDIA_MAX_BYTES = 20 * 1024 * 1024
MARKDOWN_MAX_BYTES = 5 * 1024 * 1024

# Extension -> the type sent to the server. `--media` and `--file` are one option.
PICTURE_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
}
FILE_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "ini": "text/x-ini",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "zip": "application/zip",
}
MEDIA_TYPES = {**PICTURE_TYPES, **FILE_TYPES}
PICTURE_KINDS = "JPEG, PNG, GIF, WebP or HEIC"
FILE_KINDS = "PDF, Word, Excel, CSV, JSON, INI, TXT, Markdown or ZIP"

# Text files that are not in FILE_TYPES: the way to send them is inside a Markdown file.
TEXT_EXTENSIONS = frozenset({
    "log", "tsv", "yaml", "yml", "toml", "xml", "html", "css", "sql", "py", "sh", "js", "ts",
    "dart", "go", "rs", "java", "c", "cpp", "h", "conf", "env",
})

NOTHING_SENT = "Nothing was sent."


def extension(path: str) -> str:
    return os.path.splitext(path or "")[1].lstrip(".").lower()


def media_mime(path: str) -> str:
    """The type to upload a picture or file as; empty when this kind of file cannot be attached."""
    return MEDIA_TYPES.get(extension(path), "")


def megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def _media_problem(path: str, to_versavoice: bool) -> str:
    """A picture or file given with `--media` or `--file`."""
    name = os.path.basename(path) or path
    if not os.path.isfile(path):
        return f"{path}: there is no file at this path."
    if not to_versavoice:
        return ""
    size = os.path.getsize(path)
    if size == 0:
        return f"{name}: the file is empty."
    if not media_mime(path):
        takes = f"A picture ({PICTURE_KINDS}) or a file ({FILE_KINDS}) can be attached."
        if extension(path) in TEXT_EXTENSIONS:
            return (f"{name}: this kind of file cannot be attached. {takes} This is a text file: "
                    "put its content in a Markdown file in your workspace (code inside a code block) "
                    "and attach that with --markdown.")
        return (f"{name}: this kind of file cannot be attached. {takes} Tell the person what the "
                "file is and where it is, or ask how they want it.")
    if size > MEDIA_MAX_BYTES:
        return (f"{name}: {megabytes(size)} is over the {megabytes(MEDIA_MAX_BYTES)} limit for one "
                "file. Send a smaller copy, or say where the file is.")
    return ""


def _markdown_problem(path: str, to_versavoice: bool) -> str:
    name = os.path.basename(path) or path
    if not os.path.isfile(path):
        return (f"--markdown {path}: there is no file at this path. Write the file in your "
                "workspace first, then send.")
    if not to_versavoice:
        return ""
    size = os.path.getsize(path)
    if size > MARKDOWN_MAX_BYTES:
        return (f"--markdown {name}: {megabytes(size)} is over the {megabytes(MARKDOWN_MAX_BYTES)} "
                "limit for one document. Send the part the person needs, or split it.")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (UnicodeDecodeError, OSError):
        return (f"--markdown {name}: this is not a text file. --markdown sends the text of a "
                "Markdown file; a picture goes in --media and any other file in --file.")
    if not text.strip():
        return f"--markdown {name}: the file is empty."
    return ""


def _url_problem(url: str) -> str:
    value = (url or "").strip()
    if value.lower().startswith(("http://", "https://")) and len(value) > len("https://"):
        return ""
    return (f"--url {url}: this is not a web address (it must start with http:// or https://). "
            "A file on this machine is attached with --markdown, --media or --file.")


def problems(media_paths, markdown_paths, urls, to_versavoice: bool = True) -> list[str]:
    """Why these attachments cannot be sent; empty when they all can.

    `to_versavoice` False is the route inside the install (VersaVoice off): files are passed as
    paths, so only "the file exists" applies.
    """
    media_paths, markdown_paths, urls = list(media_paths or []), list(markdown_paths or []), list(urls or [])
    found = []
    total = len(media_paths) + len(markdown_paths) + len(urls)
    if to_versavoice and total > MAX_ATTACHMENTS:
        found.append(f"{total} attachments: a message carries at most {MAX_ATTACHMENTS}. "
                     "Send fewer, or put several documents into one Markdown file.")
    found += [_media_problem(path, to_versavoice) for path in media_paths]
    found += [_markdown_problem(path, to_versavoice) for path in markdown_paths]
    if to_versavoice:
        found += [_url_problem(url) for url in urls]
    return [problem for problem in found if problem]


def refusal(found: list[str]) -> str:
    """The error `message send` answers with: what is wrong, and that the message did not go."""
    return (f"{NOTHING_SENT} " + " ".join(found) +
            " Fix this and send the message again with every attachment it names.")


def text_with_links(text: str, urls) -> str:
    """The message text for the route inside the install, which has no link attachment."""
    links = [url.strip() for url in (urls or []) if (url or "").strip()]
    if not links:
        return text
    return text + "\n\nLinks:\n" + "\n".join(f"- {link}" for link in links)
