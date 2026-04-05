"""
gen_json_offsets.py  —  Rebuild worldmap_darkmode.json from current game files.

Extracts the current vanilla worldmapview.css from the game's PAZ archives,
recalculates byte offsets for every color defined in colors.json, and writes
an updated worldmap_darkmode.json ready for the JSON mod manager.

Usage:
    gen_json_offsets.bat                              # recommended (auto-detects game)
    python gen_json_offsets.py <game_dir>             # direct, explicit game path
    python gen_json_offsets.py <game_dir> --dump-css vanilla.css

Requirements:
    pip install lz4 cryptography
"""

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Inline ChaCha20 key derivation  (mirrors paz_crypto.py / patch.py)
# ---------------------------------------------------------------------------
_HASH_INITVAL = 0x000C5EDE
_IV_XOR       = 0x60616263
_XOR_DELTAS   = [0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
                 0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202]


def _r32(v, k): return ((v << k) | (v >> (32 - k))) & 0xFFFFFFFF
def _a32(a, b): return (a + b) & 0xFFFFFFFF
def _s32(a, b): return (a - b) & 0xFFFFFFFF


def _hashlittle(data: bytes, initval: int = 0) -> int:
    length = len(data)
    a = b = c = _a32(0xDEADBEEF + length, initval)
    off = 0
    while length > 12:
        a = _a32(a, struct.unpack_from('<I', data, off)[0])
        b = _a32(b, struct.unpack_from('<I', data, off + 4)[0])
        c = _a32(c, struct.unpack_from('<I', data, off + 8)[0])
        a = _s32(a, c); a ^= _r32(c,  4); c = _a32(c, b)
        b = _s32(b, a); b ^= _r32(a,  6); a = _a32(a, c)
        c = _s32(c, b); c ^= _r32(b,  8); b = _a32(b, a)
        a = _s32(a, c); a ^= _r32(c, 16); c = _a32(c, b)
        b = _s32(b, a); b ^= _r32(a, 19); a = _a32(a, c)
        c = _s32(c, b); c ^= _r32(b,  4); b = _a32(b, a)
        off += 12; length -= 12
    tail = data[off:] + b'\x00' * 12
    if   length >= 12: c = _a32(c, struct.unpack_from('<I', tail, 8)[0])
    elif length >= 9:  c = _a32(c, struct.unpack_from('<I', tail, 8)[0] & (0xFFFFFFFF >> (8 * (12 - length))))
    if   length >= 8:  b = _a32(b, struct.unpack_from('<I', tail, 4)[0])
    elif length >= 5:  b = _a32(b, struct.unpack_from('<I', tail, 4)[0] & (0xFFFFFFFF >> (8 *  (8 - length))))
    if   length >= 4:  a = _a32(a, struct.unpack_from('<I', tail, 0)[0])
    elif length >= 1:  a = _a32(a, struct.unpack_from('<I', tail, 0)[0] & (0xFFFFFFFF >> (8 *  (4 - length))))
    elif length == 0:  return c
    c ^= b; c = _s32(c, _r32(b, 14))
    a ^= c; a = _s32(a, _r32(c, 11))
    b ^= a; b = _s32(b, _r32(a, 25))
    c ^= b; c = _s32(c, _r32(b, 16))
    a ^= c; a = _s32(a, _r32(c,  4))
    b ^= a; b = _s32(b, _r32(a, 14))
    c ^= b; c = _s32(c, _r32(b, 24))
    return c


def _chacha20_crypt(data: bytes, filename: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    basename = os.path.basename(filename).lower()
    seed = _hashlittle(basename.encode('utf-8'), _HASH_INITVAL)
    iv   = struct.pack('<I', seed) * 4
    key  = b''.join(struct.pack('<I', (seed ^ _IV_XOR) ^ d) for d in _XOR_DELTAS)
    return Cipher(algorithms.ChaCha20(key, iv), mode=None).encryptor().update(data)


# ---------------------------------------------------------------------------
# PAMT scanning to locate worldmapview.css
# ---------------------------------------------------------------------------
CSS_FILENAME = 'worldmapview.css'


def _parse_pamt_for_css(data: bytes, paz_dir: str, stem_num: int):
    """Parse a PAMT blob; return (paz_path, offset, comp_size, orig_size) or None."""
    if len(data) < 8:
        return None
    off = 0
    off += 4  # magic
    if off + 4 > len(data):
        return None
    paz_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 8
    for i in range(paz_count):
        off += 8
        if i < paz_count - 1:
            off += 4

    folder_size = struct.unpack_from('<I', data, off)[0]; off += 4
    folder_end  = off + folder_size
    folder_prefix = ''
    while off < folder_end:
        parent = struct.unpack_from('<I', data, off)[0]
        slen   = data[off + 4]
        name   = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        if parent == 0xFFFFFFFF:
            folder_prefix = name
        off += 5 + slen

    node_size  = struct.unpack_from('<I', data, off)[0]; off += 4
    node_start = off
    nodes: dict = {}
    while off < node_start + node_size:
        rel    = off - node_start
        parent = struct.unpack_from('<I', data, off)[0]
        slen   = data[off + 4]
        name   = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        nodes[rel] = (parent, name)
        off += 5 + slen

    def _build_path(node_ref):
        parts = []
        cur = node_ref
        while cur != 0xFFFFFFFF and len(parts) < 64:
            if cur not in nodes:
                break
            p, n = nodes[cur]
            parts.append(n)
            cur = p
        return ''.join(reversed(parts))

    folder_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 4
    off += folder_count * 16

    target = CSS_FILENAME.lower()
    while off + 20 <= len(data):
        node_ref, paz_offset, comp_size, orig_size, flags = \
            struct.unpack_from('<IIIII', data, off)
        off += 20
        node_path = _build_path(node_ref)
        full_path = f'{folder_prefix}/{node_path}' if folder_prefix else node_path
        if full_path.lower().endswith(target):
            paz_index = flags & 0xFF
            paz_num   = stem_num + paz_index
            paz_file  = Path(paz_dir) / f'{paz_num}.paz'
            return paz_file, paz_offset, comp_size, orig_size

    return None


def find_css_in_game(game_dir: Path):
    """
    Scan all *.pamt files under game_dir.
    Returns (paz_path, offset, comp_size, orig_size, source_group).
    source_group is the subfolder name (e.g. '0012').
    """
    pamt_files = sorted(game_dir.glob('*/0.pamt'))
    if not pamt_files:
        raise FileNotFoundError(f'No *.pamt files found under {game_dir}')

    for pamt_path in pamt_files:
        try:
            data = pamt_path.read_bytes()
        except OSError:
            continue
        try:
            stem_num = int(pamt_path.stem)
        except ValueError:
            stem_num = 0
        result = _parse_pamt_for_css(data, str(pamt_path.parent), stem_num)
        if result is not None:
            paz_path, offset, comp_size, orig_size = result
            source_group = pamt_path.parent.name
            return paz_path, offset, comp_size, orig_size, source_group

    raise FileNotFoundError(f'{CSS_FILENAME} not found in any PAMT under {game_dir}')


def extract_css(paz_path: Path, offset: int, comp_size: int, orig_size: int) -> bytes:
    """Read, ChaCha20-decrypt, and LZ4-decompress the CSS entry from a PAZ file."""
    import lz4.block
    with open(paz_path, 'rb') as f:
        f.seek(offset)
        raw = f.read(comp_size)
    decrypted = _chacha20_crypt(raw, CSS_FILENAME)
    return lz4.block.decompress(decrypted, uncompressed_size=orig_size)


# ---------------------------------------------------------------------------
# Offset calculation
# ---------------------------------------------------------------------------

def _find_color_offsets(css: bytes, search_prefix: str, vanilla_hex6: str,
                        preset: str | None) -> list[int]:
    """
    Find all byte offsets in `css` where `vanilla_hex6` starts, immediately
    preceded by `search_prefix`.

    If `preset` is given (e.g. 'preset-worldmap'), the search is scoped to the
    matching `@material-param preset-name { ... }` block only, with an exact
    word-boundary check so 'preset-worldmap' does not match inside
    'preset-worldmap-overfog'.

    For global entries (no preset), all occurrences are returned.
    """
    prefix_b  = search_prefix.encode('ascii')
    vanilla_b = vanilla_hex6.encode('ascii')

    if preset:
        # Locate the exact @material-param block for this preset.
        # Must be followed by a non-identifier byte (word boundary).
        block_header = f'@material-param {preset}'.encode('ascii')
        _IDENT = frozenset(b'abcdefghijklmnopqrstuvwxyz0123456789-_')
        pos = 0
        block_start = -1
        while True:
            idx = css.find(block_header, pos)
            if idx == -1:
                return []
            end_of_name = idx + len(block_header)
            next_byte = css[end_of_name:end_of_name + 1]
            # Only accept if not followed by another identifier character
            if not next_byte or next_byte[0] not in _IDENT:
                block_start = idx
                break
            pos = idx + 1

        # Find matching braces to delimit the block.
        brace_open = css.find(b'{', block_start)
        if brace_open == -1:
            return []
        depth = 0
        block_end = brace_open
        for i in range(brace_open, len(css)):
            if css[i] == ord('{'):
                depth += 1
            elif css[i] == ord('}'):
                depth -= 1
                if depth == 0:
                    block_end = i
                    break

        # Search for the color within the block only.
        block = css[block_start:block_end + 1]
        idx = block.find(prefix_b)
        if idx == -1:
            return []
        color_start = idx + len(prefix_b)
        if block[color_start:color_start + 6].lower() == vanilla_b:
            return [block_start + color_start]
        return []

    else:
        # Global: find all occurrences.
        offsets = []
        pos = 0
        while True:
            idx = css.find(prefix_b, pos)
            if idx == -1:
                break
            color_start = idx + len(prefix_b)
            if css[color_start:color_start + 6].lower() == vanilla_b:
                offsets.append(color_start)
            pos = idx + 1
        return offsets


def _make_patches(css: bytes, base_offset: int,
                  vanilla_hex6: str, mod_hex6_raw: str, label: str) -> list[dict]:
    """
    Generate minimal byte-level patch entries by grouping consecutive
    differing characters.  Comparison is case-insensitive; the `patched`
    field preserves the original case from colors.json (e.g. '#55513C').

    Returns a list of patch dicts  { offset, label, original, patched }.
    """
    mod_lower = mod_hex6_raw.lower()
    patches   = []
    i = 0
    while i < 6:
        if vanilla_hex6[i] != mod_lower[i]:
            j = i + 1
            while j < 6 and vanilla_hex6[j] != mod_lower[j]:
                j += 1
            orig_bytes = css[base_offset + i:base_offset + j]
            patches.append({
                'offset':   base_offset + i,
                'label':    label,
                'original': orig_bytes.hex(),
                'patched':  mod_hex6_raw[i:j].encode('ascii').hex(),
            })
            i = j
        else:
            i += 1
    return patches


def build_patches(css: bytes, colors: list[dict]) -> list[dict]:
    """
    For each entry in colors.json, locate all occurrences of the vanilla
    color in `css` and generate the corresponding byte-level patch entries.
    Returns a flat list of patch dicts sorted by offset.
    """
    all_patches = []

    for entry in colors:
        vanilla_str = entry['vanilla']  # e.g. "_originalColorTint: color, #494b4eff;"
        mod_color   = entry['mod']      # e.g. "#030405" or "#55513C"
        preset      = entry.get('preset')

        # --- Parse vanilla_str ---
        # Everything from start up to and including '#' is our search prefix.
        hash_idx = vanilla_str.index('#')
        search_prefix = vanilla_str[:hash_idx + 1]   # includes '#'
        color_raw     = vanilla_str[hash_idx + 1:].rstrip(';').lower()

        # Extract full hex string (6 or 8 chars) and the 6-char base
        m = re.match(r'([0-9a-f]{6,8})', color_raw)
        vanilla_full = m.group(1) if m else color_raw[:6]   # may be 8 chars (with alpha)
        vanilla_hex6 = vanilla_full[:6]

        # 6-char mod color, case-preserved (for patched bytes / label)
        mod_hex6_raw = mod_color.lstrip('#')[:6]

        # Skip no-op entries (mod == vanilla)
        if mod_hex6_raw.lower() == vanilla_hex6:
            continue

        # --- Build label ---
        prop  = vanilla_str.split(':')[0].strip()
        label = f'{prop}: #{vanilla_full} -> {mod_color}'
        if preset:
            label += f' [{preset}]'

        # --- Find occurrences ---
        found_offsets = _find_color_offsets(css, search_prefix, vanilla_hex6, preset)
        if not found_offsets:
            loc = f' in preset {preset}' if preset else ''
            print(f'  WARNING: "{prop}" #{vanilla_hex6} not found{loc} — skipped',
                  file=sys.stderr)
            continue

        for base_offset in found_offsets:
            patches = _make_patches(css, base_offset, vanilla_hex6, mod_hex6_raw, label)
            all_patches.extend(patches)

    all_patches.sort(key=lambda p: p['offset'])
    return all_patches


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    here = Path(__file__).parent

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('game_dir',
                    help='Crimson Desert root directory (use gen_json_offsets.bat for auto-detection)')
    ap.add_argument('--colors', default=None,
                    help='Path to colors.json  (auto-detected from repo by default)')
    ap.add_argument('--output', default=None,
                    help='Output JSON path  (default: worldmap-darkmode-JSON/worldmap_darkmode.json)')
    ap.add_argument('--dump-css', default=None, metavar='PATH',
                    help='Also save the extracted vanilla CSS to this file')
    args = ap.parse_args()

    game_dir = Path(args.game_dir)
    if not game_dir.is_dir():
        sys.exit(f'ERROR: game_dir not found: {game_dir}')

    # --- Locate colors.json ---
    # Script lives in json-generator/; repo root is one level up.
    repo_root = here.parent
    if args.colors:
        colors_path = Path(args.colors)
    else:
        colors_path = repo_root / 'worldmap-darkmode-PYTHON' / 'colors.json'
        if not colors_path.exists():
            sys.exit(f'ERROR: colors.json not found at {colors_path}\nUse --colors <path> to specify it')

    # --- Locate output path ---
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = here / 'worldmap_darkmode.json'

    # --- Load colors ---
    colors = json.loads(colors_path.read_text(encoding='utf-8'))
    print(f'  Loaded {len(colors)} color entries')

    # --- Extract vanilla CSS from game ---
    print(f'  Scanning PAMTs...')
    paz_path, offset, comp_size, orig_size, source_group = find_css_in_game(game_dir)
    print(f'  Found {CSS_FILENAME} in {paz_path.name} (group {source_group})'
          f'  [{comp_size:,} -> {orig_size:,} bytes]')

    css_bytes = extract_css(paz_path, offset, comp_size, orig_size)
    print(f'  Extracted CSS: {len(css_bytes):,} bytes')

    if args.dump_css:
        Path(args.dump_css).write_bytes(css_bytes)
        print(f'  Saved vanilla CSS → {args.dump_css}')

    # --- Calculate offsets & build patches ---
    print(f'  Calculating offsets...')
    patches = build_patches(css_bytes, colors)
    print(f'  Generated {len(patches)} patch entries')

    # --- Load existing modinfo (preserve version/author/etc.) ---
    modinfo = {
        'title':       'Dark Mode Map',
        'version':     '1.2',
        'author':      'TheLastGunslinger9',
        'description': 'Replaces the world map color palette with a dark theme.',
        'nexus_url':   'https://www.nexusmods.com/crimsondesert/mods/411',
    }
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding='utf-8'))
            modinfo  = existing.get('modinfo', modinfo)
        except Exception:
            pass

    output = {
        'modinfo': modinfo,
        'patches': [
            {
                'game_file':    f'ui/{CSS_FILENAME}',
                'source_group': source_group,
                'changes':      patches,
            }
        ],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(f'  Wrote {out_path.name}')


if __name__ == '__main__':
    main()
