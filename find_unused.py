"""
find_unused.py - find files in rip/ never referenced by any HTML or CSS file.

Handles:
  - href / src / srcset / action / data / poster / background attributes in HTML
  - url() and @import in CSS files and inline <style> blocks
  - Absolute /oficinadossonhos/... paths, other absolute paths, and relative paths
  - CSS url() resolved relative to the CSS file's own directory

Usage:
    python find_unused.py
"""

import os
import re
import sys
import html as html_mod
from urllib.parse import unquote

RIP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rip')
SITE_PREFIX = '/oficinadossonhos/'

ATTR_RE = re.compile(
    r'\b(href|src|action|srcset|data|poster|background)\s*=\s*(["\'])([^"\']*)\2',
    re.IGNORECASE
)
# url() with optional quotes; avoids matching data: URIs via the normalize step
CSS_URL_RE = re.compile(r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)', re.IGNORECASE)
# @import "..." or @import url("...")
CSS_IMPORT_RE = re.compile(
    r'@import\s+(?:url\(\s*)?["\']([^"\']+)["\'](?:\s*\))?',
    re.IGNORECASE
)
# style="..." or style='...'  (non-dotall to stay within the attribute value)
STYLE_ATTR_RE = re.compile(
    r'\bstyle\s*=\s*"([^"]*)"|\bstyle\s*=\s*\'([^\']*)\'',
    re.IGNORECASE
)
STYLE_BLOCK_RE = re.compile(r'<style[^>]*>(.*?)</style>', re.IGNORECASE | re.DOTALL)

SKIP_SCHEMES = ('http:', 'https:', 'ftp:', '//', 'mailto:', 'javascript:', 'data:', '#')


def normalize(raw, base_dir):
    """Resolve a raw URL string to an absolute path inside rip/, or None to skip."""
    value = unquote(html_mod.unescape(raw)).strip()
    if not value:
        return None

    lower = value.lower()
    if any(lower.startswith(s) for s in SKIP_SCHEMES):
        return None

    # Strip fragment and query string before resolving
    for sep in ('#', '?'):
        if sep in value:
            value = value[:value.index(sep)]
    if not value:
        return None

    if value.startswith('/'):
        if value.startswith(SITE_PREFIX):
            rel = value[len(SITE_PREFIX):]
        else:
            rel = value.lstrip('/')
        path = os.path.normpath(os.path.join(RIP_DIR, rel.replace('/', os.sep)))
    else:
        path = os.path.normpath(os.path.join(base_dir, value.replace('/', os.sep)))

    # Reject paths that escape rip/
    if not path.startswith(os.path.abspath(RIP_DIR) + os.sep) and path != os.path.abspath(RIP_DIR):
        return None

    return path


def refs_from_css(content, base_dir):
    """Yield resolved paths from a block of CSS text."""
    for m in CSS_URL_RE.finditer(content):
        p = normalize(m.group(1), base_dir)
        if p:
            yield p
    for m in CSS_IMPORT_RE.finditer(content):
        p = normalize(m.group(1), base_dir)
        if p:
            yield p


def collect_html_refs(html_file_abs):
    base_dir = os.path.dirname(html_file_abs)
    refs = set()
    try:
        with open(html_file_abs, encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return refs

    # Tag attributes
    for m in ATTR_RE.finditer(content):
        attr = m.group(1).lower()
        raw = m.group(3)
        if attr == 'srcset':
            # "img-400.jpg 400w, img-800.jpg 800w"
            for part in raw.split(','):
                token = part.strip().split()[0] if part.strip() else ''
                p = normalize(token, base_dir)
                if p:
                    refs.add(p)
        else:
            p = normalize(raw, base_dir)
            if p:
                refs.add(p)

    # Inline style attributes
    for m in STYLE_ATTR_RE.finditer(content):
        style_content = m.group(1) or m.group(2) or ''
        refs.update(refs_from_css(style_content, base_dir))

    # <style> blocks
    for m in STYLE_BLOCK_RE.finditer(content):
        refs.update(refs_from_css(m.group(1), base_dir))

    return refs


def collect_css_refs(css_file_abs):
    base_dir = os.path.dirname(css_file_abs)
    refs = set()
    try:
        with open(css_file_abs, encoding='utf-8', errors='replace') as f:
            content = f.read()
        refs.update(refs_from_css(content, base_dir))
    except OSError:
        pass
    return refs


def format_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f} {unit}'
        n /= 1024


def print_file_list(files_dict, rip_parent):
    total = 0
    for path in sorted(files_dict):
        size = files_dict[path]
        total += size
        rel = os.path.relpath(path, rip_parent)
        print(f'  {rel}  ({format_size(size)})')
    return total


def ask_delete(files_dict, label):
    total_size = format_size(sum(files_dict.values()))
    try:
        answer = input(f'\nDelete {len(files_dict)} {label}? [{total_size} freed] [y/N] ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer == 'y':
        deleted = 0
        freed = 0
        for path, size in files_dict.items():
            try:
                os.remove(path)
                deleted += 1
                freed += size
            except OSError as e:
                print(f'  ERROR: {e}')
        print(f'  Deleted {deleted} file(s), freed {format_size(freed)}.')
    else:
        print('  Skipped.')


def main():
    if not os.path.isdir(RIP_DIR):
        print(f'ERROR: rip/ directory not found at {RIP_DIR}')
        sys.exit(1)

    rip_parent = os.path.dirname(RIP_DIR)
    rip_abs = os.path.abspath(RIP_DIR)

    # ── Step 1: inventory ──────────────────────────────────────────────────────
    print('Scanning rip/ for files...')
    all_files = {}   # abs_path -> size
    html_files = []
    css_files = []

    for dirpath, _dirs, filenames in os.walk(RIP_DIR):
        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = 0
            all_files[abs_path] = size
            lower = fname.lower()
            if lower.endswith('.html'):
                html_files.append(abs_path)
            elif lower.endswith('.css'):
                css_files.append(abs_path)

    print(f'  {len(all_files)} files  ({len(html_files)} HTML, {len(css_files)} CSS)')

    # ── Step 2: collect all references ────────────────────────────────────────
    print('Collecting references...')
    referenced = set()

    for i, f in enumerate(html_files, 1):
        if i % 100 == 0:
            print(f'  HTML {i}/{len(html_files)}...', end='\r')
        referenced.update(collect_html_refs(f))

    for i, f in enumerate(css_files, 1):
        if i % 20 == 0:
            print(f'  CSS  {i}/{len(css_files)}...', end='\r')
        referenced.update(collect_css_refs(f))

    print(f'  {len(referenced)} unique file references collected.        ')

    # ── Step 3: diff ──────────────────────────────────────────────────────────
    unreferenced = {p: s for p, s in all_files.items() if p not in referenced}

    if not unreferenced:
        print('\nEverything in rip/ is referenced. Nothing to clean up.')
        return

    assets     = {p: s for p, s in unreferenced.items() if not p.lower().endswith('.html')}
    orphan_html = {p: s for p, s in unreferenced.items() if p.lower().endswith('.html')}

    # ── Step 4: report ────────────────────────────────────────────────────────
    if assets:
        print(f'\n{"=" * 62}')
        print(f'UNREFERENCED ASSETS  ({len(assets)} files)')
        print('=' * 62)
        asset_total = print_file_list(assets, rip_parent)
        print(f'\n  Subtotal: {format_size(asset_total)}')

    if orphan_html:
        print(f'\n{"=" * 62}')
        print(f'ORPHANED HTML PAGES  ({len(orphan_html)} files -- not linked from any other file)')
        print('=' * 62)
        html_total = print_file_list(orphan_html, rip_parent)
        print(f'\n  Subtotal: {format_size(html_total)}')

    grand_total = sum(unreferenced.values())
    print(f'\n  GRAND TOTAL: {len(unreferenced)} unreferenced file(s) -- {format_size(grand_total)} recoverable')

    # ── Step 5: delete prompts ────────────────────────────────────────────────
    if assets:
        ask_delete(assets, 'unreferenced assets')

    if orphan_html:
        ask_delete(orphan_html, 'orphaned HTML pages')


if __name__ == '__main__':
    main()
