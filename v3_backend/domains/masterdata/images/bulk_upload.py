"""Pure functions for bulk product-image ZIP upload (2026-06-22).

Two responsibilities, neither touches the DB or storage:

  1. `build_template_zip(...)` — generate the empty directory-tree
     ZIP a user downloads. Tree is `<country>/<port>/<product_code>/`
     so when they drop images into the leaf folders and re-zip, the
     path itself is the identity key.

  2. `parse_uploaded_zip(...)` — given raw ZIP bytes, return a list of
     `ParsedEntry` records (one per image file inside). Caller does the
     DB matching.

Both functions are deterministic and DB-free so they unit-test in
milliseconds. The HTTP layer + service layer wrap them with persistence
and matching.

Security:
  - parse_uploaded_zip rejects "zip slip" paths (`..`, absolute, drive
    letters) before extraction is even considered. We never call
    `ZipFile.extract()` — only `.read(name)` into memory, gated by
    per-file size.
  - macOS metadata folders (`__MACOSX/`, `.DS_Store`) are silently
    skipped to avoid noisy "unmatched" entries.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

# ─── Constants ────────────────────────────────────────────────

# Recognized image extensions — matches the existing single-image
# upload (`add_product_image`) ALLOWED_MIME_TYPES set so we don't
# silently accept formats the downstream pipeline will reject.
ALLOWED_IMAGE_EXT: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Per-image size cap (5 MB). Mirrors `_product_images_service.MAX_IMAGE_BYTES`.
# Bigger images would also be rejected downstream by `add_product_image`,
# but failing at parse time gives the user a single coherent error report
# rather than a mid-commit explosion.
MAX_IMAGE_BYTES: int = 5 * 1024 * 1024

# Total ZIP size cap. Cloud Run has a 32 MB request limit by default;
# we bound the unzipped payload at 100 MB to keep memory predictable
# during parsing — beyond this, recommend the user split into multiple
# uploads. The HTTP layer enforces the request-side cap; this constant
# is the post-decode upper bound on what we iterate over.
MAX_UNZIPPED_TOTAL_BYTES: int = 100 * 1024 * 1024


@dataclass(frozen=True)
class ParsedEntry:
    """One image file found inside a bulk-upload ZIP, prior to matching."""

    zip_path: str
    """Path inside the ZIP, verbatim. Used for error messages."""
    country_name: str
    port_name: str
    product_code: str
    image_filename: str
    content: bytes
    error: str | None = None
    """Set when the entry was parseable as a path but fails validation
    (oversize, bad extension). The caller stages it as `status=error`."""


@dataclass(frozen=True)
class TemplateProductRow:
    """Input row for `build_template_zip` — one product per leaf folder.

    The service layer queries the DB for these rows; this dataclass is
    the boundary so the template builder stays pure.
    """

    country_name: str
    port_name: str
    product_code: str
    product_name: str = ""
    """English product name appended to the folder for readability.
    Folder format: ``<code> — <name>`` (em dash with single-space
    padding). Em dash is chosen because product codes commonly contain
    plain ASCII '-' (e.g. `BEEF-001`) — splitting on em dash avoids the
    ambiguity. Empty string falls back to code-only naming (the old
    behavior, preserved for backward compat)."""
    has_images: bool = False
    """If True, the folder is rendered with a ✓ marker so users can
    skip products that already have images on a补图 pass."""


# ─── Template builder ────────────────────────────────────────


def build_template_zip(
    products: list[TemplateProductRow],
    *,
    include_instructions: bool = True,
) -> bytes:
    """Return ZIP bytes containing a `<country>/<port>/<product_code>/`
    directory tree, one empty leaf folder per product.

    Empty folders are encoded as zero-byte entries with a trailing `/`
    in the name — standard ZIP convention. Most file managers (macOS
    Finder, Windows Explorer) extract these as real empty directories.

    `products` ordering controls the order users see in their file
    manager; sort upstream (country → port → code) for predictable UX.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Track which intermediate dirs we've already emitted — ZIPs
        # don't strictly need explicit dir entries (most tools infer
        # them from leaf-file paths), but emitting them makes the
        # extracted tree show up consistently across Finder/Explorer/
        # 7-Zip even when leaves are empty.
        emitted_dirs: set[str] = set()

        def _emit_dir(path: str) -> None:
            if path and path not in emitted_dirs:
                # ZipInfo entries ending in `/` are directories.
                zf.writestr(path if path.endswith("/") else path + "/", b"")
                emitted_dirs.add(path)

        for row in products:
            country = _safe_segment(row.country_name)
            port = _safe_segment(row.port_name)
            code_safe = _safe_segment(row.product_code)
            # Folder display name: `<code> — <name> ✓?`
            #   - em dash separator (' — ') so we can split unambiguously
            #     at parse time even when product_code contains '-'
            #   - name is purely cosmetic — users can rename, edit, or
            #     even leave it out and parsing still recovers the code
            #   - trailing ✓ on already-imaged products lets补图 users
            #     skip them at a glance; strip()ed away during parse
            label = code_safe
            if row.product_name:
                label = f"{code_safe} — {_safe_segment(row.product_name)}"
            if row.has_images:
                label = f"{label} ✓"
            _emit_dir(f"products/{country}")
            _emit_dir(f"products/{country}/{port}")
            _emit_dir(f"products/{country}/{port}/{label}")

        if include_instructions:
            stats = (
                f"产品总数: {len(products)}\n"
                f"已有图片: {sum(1 for p in products if p.has_images)}\n"
                f"暂无图片: {sum(1 for p in products if not p.has_images)}\n"
            )
            zf.writestr(
                "Instructions.txt",
                _INSTRUCTIONS_TEMPLATE.format(stats=stats).encode("utf-8"),
            )

    return buf.getvalue()


# ─── ZIP parser ──────────────────────────────────────────────


def parse_uploaded_zip(zip_bytes: bytes) -> tuple[list[ParsedEntry], list[str]]:
    """Decode user-uploaded ZIP into `(entries, warnings)`.

    Returns:
        entries: one `ParsedEntry` per legitimate image file, including
                 ones that fail per-file validation (their `error` field
                 is populated). Empty folders, hidden dotfiles, and
                 macOS metadata are skipped silently — they don't
                 belong in the report.
        warnings: high-level issues that aren't tied to a specific
                  entry (e.g. "total unzipped size exceeds 100MB" —
                  we still parsed what we could, but the user should
                  know we stopped early).

    Raises `ValueError` for hard-fatal corruption (not a valid ZIP).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"ZIP 解析失败：{exc}") from exc

    entries: list[ParsedEntry] = []
    warnings: list[str] = []
    total_bytes = 0

    for info in zf.infolist():
        # Directories — silently skipped. Their existence in the
        # template is what conveys "this product has no images yet";
        # we only ingest leaf files.
        if info.is_dir():
            continue

        # ZIP encoding fix (2026-07-01): if the producer didn't set the
        # UTF-8 general-purpose flag (bit 0x800), Python's zipfile
        # falls back to cp437 — UTF-8 bytes like the em dash separator
        # `—` (E2 80 94) get decoded as `ΓÇö`, and our `parse_folder_code`
        # then can't find the separator. Re-decode via cp437 → UTF-8 to
        # recover the original name. No-op for ZIPs that DID set the
        # UTF-8 flag (Python already decoded them correctly).
        name = _normalize_zip_filename(info)
        # Reject zip-slip / absolute paths defensively. We never call
        # `extract()`, but defense in depth — if someone wires this
        # into a future code path that does extract, the guard is here.
        if (
            ".." in name.split("/")
            or name.startswith("/")
            or (len(name) > 1 and name[1] == ":")
        ):
            warnings.append(f"跳过可疑路径 {name!r}（zip slip 防御）")
            continue

        # Skip macOS resource forks and hidden metadata.
        if "__MACOSX/" in name or "/.DS_Store" in name or name.endswith("/.DS_Store"):
            continue
        # Skip generic dotfiles in any segment.
        segments = [s for s in name.split("/") if s]
        if any(s.startswith(".") for s in segments):
            continue

        # Skip the Instructions.txt artifact anywhere in the tree —
        # users sometimes leave it in when they re-zip, and the wrapping
        # folder name varies (`products/`, `product-images-template-XXX/`,
        # etc.) so we match on the basename, not the full path.
        if segments and segments[-1] == "Instructions.txt":
            continue

        # Path must be at least 4 segments deep:
        # `products/<country>/<port>/<product_code>/<filename>`.
        # The `products/` prefix is the template convention; we tolerate
        # uploads without it too (user might have re-zipped from the
        # leaf level) — anything ≥ 4 segments works as long as the
        # last 4 form (country, port, code, filename).
        if len(segments) < 4:
            # Files at < 4 depth are either junk (loose files at the
            # root) or template Instructions — report as warning so
            # the user knows we noticed.
            warnings.append(f"跳过不符合层级的文件 {name!r}（需要 country/port/code/file）")
            continue

        # Always take the LAST 4 segments — this lets us tolerate the
        # `products/` prefix that the template ships with, or its
        # absence if the user re-zipped from inside.
        country, port, code_raw, filename = segments[-4:]
        product_code = _parse_folder_code(code_raw)

        # Extension whitelist — bad extensions become error entries
        # (visible to user in the report), not silent skips, so the
        # user knows their .heic file was rejected.
        ext = _extension(filename).lower()
        if ext not in ALLOWED_IMAGE_EXT:
            entries.append(
                ParsedEntry(
                    zip_path=name,
                    country_name=country.strip(),
                    port_name=port.strip(),
                    product_code=product_code,
                    image_filename=filename,
                    content=b"",
                    error=f"不支持的扩展名 {ext!r}（仅支持 jpg / jpeg / png / webp）",
                )
            )
            continue

        # Read content with the per-file + total-size guards.
        size = info.file_size
        if size > MAX_IMAGE_BYTES:
            entries.append(
                ParsedEntry(
                    zip_path=name,
                    country_name=country.strip(),
                    port_name=port.strip(),
                    product_code=product_code,
                    image_filename=filename,
                    content=b"",
                    error=f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限（{size // 1024}KB）",
                )
            )
            continue
        if total_bytes + size > MAX_UNZIPPED_TOTAL_BYTES:
            warnings.append(
                f"ZIP 解压总大小超过 {MAX_UNZIPPED_TOTAL_BYTES // (1024 * 1024)}MB，"
                f"停止读取剩余文件。已解析 {len(entries)} 个。"
            )
            break

        try:
            content = zf.read(info)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            entries.append(
                ParsedEntry(
                    zip_path=name,
                    country_name=country.strip(),
                    port_name=port.strip(),
                    product_code=product_code,
                    image_filename=filename,
                    content=b"",
                    error=f"读取失败：{exc}",
                )
            )
            continue

        total_bytes += len(content)
        entries.append(
            ParsedEntry(
                zip_path=name,
                country_name=country.strip(),
                port_name=port.strip(),
                product_code=product_code,
                image_filename=filename,
                content=content,
            )
        )

    return entries, warnings


# ─── Helpers ─────────────────────────────────────────────────


# Strip filesystem-hostile characters from a path segment so the
# downloaded template extracts cleanly on Windows / macOS / Linux.
# Conservative: only kill `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`
# (Windows-reserved + tree-breaking) and squeeze whitespace.
_SAFE_SEGMENT_RE = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE_RE = re.compile(r"\s+")


def _safe_segment(s: str) -> str:
    if not s:
        return "_"
    s = _SAFE_SEGMENT_RE.sub("_", s).strip()
    s = _WHITESPACE_RE.sub(" ", s)
    return s or "_"


def _extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:] if idx > 0 else ""


def _normalize_zip_filename(info: zipfile.ZipInfo) -> str:
    """Recover the original UTF-8 filename from a ZIP entry whose
    producer didn't set the UTF-8 general-purpose flag (bit 0x800).

    Background: Python's `zipfile` reads the raw bytes from the central
    directory and decodes via UTF-8 when the flag is set, cp437 otherwise.
    Some Windows tools and even certain macOS workflows produce ZIPs
    without the flag but with UTF-8-encoded names — Python then yields
    strings like `99PRD010601 ΓÇö ASPARAGUS GREEN LARGE` where the
    `ΓÇö` is the cp437 view of bytes `E2 80 94` (UTF-8 for em dash `—`).

    Detection: we test for the flag and only round-trip when it's clear.
    If the flag was set, Python's decoding was already correct — never
    touch it. If the round-trip itself can't encode/decode cleanly,
    leave the name alone (better an "unmatched" report row than a
    crash).
    """
    raw_name = info.filename
    if info.flag_bits & 0x800:
        # Producer claimed UTF-8; trust Python's decoding.
        return raw_name
    try:
        return raw_name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Either the original bytes weren't valid UTF-8, or the string
        # contained characters outside cp437. Fall back to whatever
        # Python gave us — at least the basename is preserved.
        return raw_name


# Em dash (U+2014) with single-space padding is the separator the
# template uses between `<code>` and `<name>`. We chose em dash
# specifically because plain hyphen (`-`) is common inside product codes
# (`BEEF-001`), so splitting on `-` would mangle the code.
_FOLDER_CODE_SEPARATOR = " — "


def _parse_folder_code(raw: str) -> str:
    """Extract the product_code from a folder name produced by the
    template builder (or one a user re-created by hand).

    The template's label format is::

        <code> — <name> ✓?

    Both ``— name`` and ``✓`` are display-only and stripped here.
    Backward-compatible with the v1 template format (code-only,
    optionally ``✓``).
    """
    s = raw.strip()
    # Strip the "already has images" marker first — it can sit at the
    # end of either the v1 (`code ✓`) or v2 (`code — name ✓`) format.
    if s.endswith("✓"):
        s = s[:-1].rstrip()
    # Split off the cosmetic name suffix on em dash.
    if _FOLDER_CODE_SEPARATOR in s:
        s = s.split(_FOLDER_CODE_SEPARATOR, 1)[0].rstrip()
    return s


# ─── Instructions text shipped inside the template ZIP ─────


_INSTRUCTIONS_TEMPLATE = """\
=== 产品图片批量上传 — 操作说明 ===

【模板结构】
  products/
    <国家>/
      <港口>/
        <产品代码> — <产品英文名>/   ← 把图片拖到这里
          xxx.jpg
          xxx.png

【关于文件夹名】

模板把文件夹命名为「产品代码 — 产品英文名」，例如:
   BEEF-001 — Beef Tenderloin Grade A/

横线两侧的空格 + em dash（—）是为了避免和产品代码本身的 `-` 混淆。
你不需要保留产品名 —— 只要 **「产品代码」部分不动**，名字部分随便改、
甚至删掉都可以。系统只按代码匹配。

文件夹名后带 ✓ 表示该产品**已有图片**:
   BEEF-001 — Beef Tenderloin Grade A ✓/   ← 这个产品已经有图了
   FISH-001 — Salmon Atlantic Whole/        ← 这个还没有图

   - 想替换 / 追加：把新图放进去
   - 不想动：留空（保留 ✓ 也行；空文件夹不会触发更新）

【上传步骤】

1. 解压本 ZIP
2. 把图片拖到对应的产品文件夹
3. 把 `products/` 整个目录重新压成 ZIP
4. 在网页点「上传 ZIP」→ 预览匹配结果 → 确认提交

【限制】

- 每张图 ≤ 5MB；支持 jpg / jpeg / png / webp
- 整个 ZIP 解压后总大小 ≤ 100MB（超过请拆分上传）

【匹配规则】

系统按 (国家, 港口, 产品代码) 三元组精确匹配。
- 文件夹名拼错（产品代码错） → 上传后在「未匹配」列表里显示
- 已有图的产品 → 新图**追加**到 gallery 末尾（不替换主图）

【模板生成时的数据快照】

{stats}
"""
