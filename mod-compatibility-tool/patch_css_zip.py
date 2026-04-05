"""
Dark Mode Map: Mod Compatibility Tool

Patches worldmapview.css inside any mod .zip with Dark Mode colors.

Usage:
    python patch_css_zip.py <path_to_mod.zip>
"""
import io, json, os, re, struct, sys, zipfile
import lz4.block
from pathlib import Path

# ---------------------------------------------------------------------------
# PAZ entry discovery via PAMT index
# ---------------------------------------------------------------------------
CSS_FILENAME = 'worldmapview.css'

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# Inline ChaCha20 key derivation (mirrors paz_crypto.py)
# Requires: cryptography  (pip install cryptography)
# ---------------------------------------------------------------------------
_HASH_INITVAL = 0x000C5EDE
_IV_XOR       = 0x60616263
_XOR_DELTAS   = [0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
                 0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202]


def _r32(v: int, k: int) -> int: return ((v << k) | (v >> (32 - k))) & 0xFFFFFFFF
def _a32(a: int, b: int) -> int: return (a + b) & 0xFFFFFFFF
def _s32(a: int, b: int) -> int: return (a - b) & 0xFFFFFFFF


def _hashlittle(data: bytes, initval: int = 0) -> int:
    length = len(data)
    a = b = c = _a32(0xDEADBEEF + length, initval)
    off = 0
    while length > 12:
        a = _a32(a, struct.unpack_from('<I', data, off)[0])
        b = _a32(b, struct.unpack_from('<I', data, off + 4)[0])
        c = _a32(c, struct.unpack_from('<I', data, off + 8)[0])
        a = _s32(a, c); a ^= _r32(c, 4);  c = _a32(c, b)
        b = _s32(b, a); b ^= _r32(a, 6);  a = _a32(a, c)
        c = _s32(c, b); c ^= _r32(b, 8);  b = _a32(b, a)
        a = _s32(a, c); a ^= _r32(c, 16); c = _a32(c, b)
        b = _s32(b, a); b ^= _r32(a, 19); a = _a32(a, c)
        c = _s32(c, b); c ^= _r32(b, 4);  b = _a32(b, a)
        off += 12; length -= 12
    tail = data[off:] + b'\x00' * 12
    if length >= 12:   c = _a32(c, struct.unpack_from('<I', tail, 8)[0])
    elif length >= 9:  c = _a32(c, struct.unpack_from('<I', tail, 8)[0] & (0xFFFFFFFF >> (8 * (12 - length))))
    if length >= 8:    b = _a32(b, struct.unpack_from('<I', tail, 4)[0])
    elif length >= 5:  b = _a32(b, struct.unpack_from('<I', tail, 4)[0] & (0xFFFFFFFF >> (8 * (8 - length))))
    if length >= 4:    a = _a32(a, struct.unpack_from('<I', tail, 0)[0])
    elif length >= 1:  a = _a32(a, struct.unpack_from('<I', tail, 0)[0] & (0xFFFFFFFF >> (8 * (4 - length))))
    elif length == 0:  return c
    c ^= b; c = _s32(c, _r32(b, 14))
    a ^= c; a = _s32(a, _r32(c, 11))
    b ^= a; b = _s32(b, _r32(a, 25))
    c ^= b; c = _s32(c, _r32(b, 16))
    a ^= c; a = _s32(a, _r32(c, 4))
    b ^= a; b = _s32(b, _r32(a, 14))
    c ^= b; c = _s32(c, _r32(b, 24))
    return c


def _chacha20_crypt(data: bytes, filename: str) -> bytes:
    """ChaCha20 encrypt/decrypt (symmetric) using key derived from filename."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    basename = os.path.basename(filename).lower()
    seed = _hashlittle(basename.encode('utf-8'), _HASH_INITVAL)
    iv  = struct.pack('<I', seed) * 4
    key = b''.join(struct.pack('<I', (seed ^ _IV_XOR) ^ d) for d in _XOR_DELTAS)
    return Cipher(algorithms.ChaCha20(key, iv), mode=None).encryptor().update(data)


# ---------------------------------------------------------------------------
# PAMT parsing (in-memory, returns paz_index instead of paz_path)
# ---------------------------------------------------------------------------

def _find_css_entry_in_pamt(data: bytes) -> tuple[int, int, int, int]:
    """Parse PAMT bytes and return (paz_index, offset, comp_size, orig_size) for worldmapview.css."""
    if len(data) < 8:
        raise ValueError('PAMT too short')

    off = 0
    off += 4  # magic
    paz_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 8  # hash + zero
    for i in range(paz_count):
        off += 8  # hash + size
        if i < paz_count - 1:
            off += 4  # separator

    # Folder section
    folder_size = struct.unpack_from('<I', data, off)[0]; off += 4
    folder_end = off + folder_size
    folder_prefix = ''
    while off < folder_end:
        parent = struct.unpack_from('<I', data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        if parent == 0xFFFFFFFF:
            folder_prefix = name
        off += 5 + slen

    # Node section (path tree)
    node_size = struct.unpack_from('<I', data, off)[0]; off += 4
    node_start = off
    nodes: dict[int, tuple[int, str]] = {}
    while off < node_start + node_size:
        rel = off - node_start
        parent = struct.unpack_from('<I', data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        nodes[rel] = (parent, name)
        off += 5 + slen

    def _build_path(node_ref: int) -> str:
        parts: list[str] = []
        cur = node_ref
        while cur != 0xFFFFFFFF and len(parts) < 64:
            if cur not in nodes:
                break
            p, n = nodes[cur]
            parts.append(n)
            cur = p
        return ''.join(reversed(parts))

    # Record section
    folder_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 4  # hash
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
            return paz_index, paz_offset, comp_size, orig_size

    raise FileNotFoundError(f'{CSS_FILENAME} not found in PAMT index')


def _find_css_in_zip(zf: zipfile.ZipFile) -> tuple[str, str, int, int, int, int]:
    """Scan PAMT files in zip for worldmapview.css.
    Returns (pamt_key, paz_key, paz_index, offset, comp_size, orig_size)."""
    namelist = zf.namelist()
    for name in namelist:
        if not name.endswith('.pamt'):
            continue
        pamt_dir = name.rsplit('/', 1)[0] + '/'
        try:
            paz_index, css_offset, comp_size, orig_size = \
                _find_css_entry_in_pamt(zf.read(name))
            paz_key = pamt_dir + f'{paz_index}.paz'
            if paz_key in namelist:
                return name, paz_key, paz_index, css_offset, comp_size, orig_size
        except Exception:
            continue
    raise FileNotFoundError(f'{CSS_FILENAME} not found in any .pamt inside zip')


def extract_css_from_bytes(paz_bytes: bytes, offset: int, comp_size: int, orig_size: int) -> bytes:
    """Decrypt and decompress CSS from in-memory PAZ bytes."""
    raw = paz_bytes[offset:offset + comp_size]
    return lz4.block.decompress(_chacha20_crypt(raw, CSS_FILENAME), uncompressed_size=orig_size)


def patch_paz_bytes(paz_bytes: bytes, offset: int, comp_size: int, compressed: bytes) -> bytes:
    """Encrypt and splice compressed CSS payload back into PAZ bytes."""
    if len(compressed) != comp_size:
        raise ValueError(f'payload is {len(compressed)} bytes, expected {comp_size}')
    payload = _chacha20_crypt(compressed, CSS_FILENAME)
    ba = bytearray(paz_bytes)
    ba[offset:offset + comp_size] = payload
    return bytes(ba)


def rewrite_zip_entry(zip_path: Path, entry_name: str, new_data: bytes,
                      rename_prefix: tuple[str, str] | None = None) -> None:
    """Rebuild zip with patched entry, optionally renaming an internal folder prefix."""
    buf = io.BytesIO()
    with zipfile.ZipFile(zip_path, 'r') as zin:
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_STORED) as zout:
            for item in zin.infolist():
                data = new_data if item.filename == entry_name else zin.read(item.filename)
                if rename_prefix:
                    old, new = rename_prefix
                    if item.filename.startswith(old):
                        item.filename = new + item.filename[len(old):]
                zout.writestr(item, data)
    zip_path.write_bytes(buf.getvalue())


def apply_color(text: str, old_str: str, cur_rgb: str) -> tuple[str, int]:
    """Global replace of hex color in old_str with cur_rgb, preserving alpha."""
    m = re.search(r'#[0-9a-fA-F]+', old_str)
    if m is None:
        raise ValueError(f'No hex color found in old_str: {old_str!r}')
    orig_hex = m.group(0)
    alpha = orig_hex[7:] if len(orig_hex) > 7 else ''
    new_hex = cur_rgb + alpha
    new_str = old_str.replace(orig_hex, new_hex)
    count = text.count(old_str)
    if count == 0:
        print(f'  WARNING: not found - {old_str.strip()[:60]}')
        return text, 0
    return text.replace(old_str, new_str), count


def apply_color_scoped(text: str, preset: str, old_str: str, cur_rgb: str) -> tuple[str, int]:
    """Replace hex color in old_str only within the @material-param <preset> block."""
    m = re.search(r'#[0-9a-fA-F]+', old_str)
    if m is None:
        raise ValueError(f'No hex color found in old_str: {old_str!r}')
    orig_hex = m.group(0)
    alpha = orig_hex[7:] if len(orig_hex) > 7 else ''
    new_hex = cur_rgb + alpha
    new_str = old_str.replace(orig_hex, new_hex)
    block_pat = re.compile(
        r'(@material-param ' + re.escape(preset) + r'\s*\{.*?\})(?=\s*@material-param |\s*\Z)',
        re.DOTALL)
    bm = block_pat.search(text)
    if not bm:
        print(f'  WARNING: preset block not found - {preset}')
        return text, 0
    block = bm.group(0)
    if old_str not in block:
        print(f'  WARNING: not found in {preset} - {old_str.strip()[:50]}')
        return text, 0
    new_block = block.replace(old_str, new_str, 1)
    return text[:bm.start()] + new_block + text[bm.end():], 1


def _pad_to_orig_size(data: bytes, orig_size: int) -> bytes:
    if len(data) >= orig_size:
        return data[:orig_size]
    return data + b'\x00' * (orig_size - len(data))


def _find_css_comments(data: bytes) -> list[tuple[int, int]]:
    """Return (content_start, content_end) for every /* ... */ comment in data."""
    comments = []
    search_from = 0
    while True:
        start = data.find(b'/*', search_from)
        if start == -1:
            break
        content_start = start + 2
        end = data.find(b'*/', content_start)
        if end == -1:
            break
        if end > content_start:
            comments.append((content_start, end))
        search_from = end + 2
    return comments


def _shrink_to_orig_size(data: bytes, orig_size: int) -> bytes:
    """Shrink CSS bytes to orig_size by trimming comment bodies and whitespace."""
    if len(data) <= orig_size:
        return _pad_to_orig_size(data, orig_size)

    excess = len(data) - orig_size
    result = bytearray(data)

    # Phase 1: trim CSS comment bodies from largest to smallest.
    comments = _find_css_comments(bytes(result))
    comments.sort(key=lambda c: c[1] - c[0], reverse=True)
    for cstart, cend in comments:
        if excess <= 0:
            break
        body_len = cend - cstart
        removable = body_len - 1  # keep at least one byte so comment stays valid
        if removable <= 0:
            continue
        to_remove = min(removable, excess)
        result[cstart + 1:cstart + 1 + to_remove] = b''
        excess -= to_remove
        comments = _find_css_comments(bytes(result))
        comments.sort(key=lambda c: c[1] - c[0], reverse=True)

    # Phase 2: collapse trailing repeated spaces/tabs.
    i = len(result) - 1
    while i > 0 and excess > 0:
        if result[i] in (0x20, 0x09) and result[i - 1] in (0x20, 0x09):
            del result[i]
            excess -= 1
        i -= 1

    if len(result) > orig_size:
        raise ValueError(
            f'Modified file is {len(data) - orig_size} bytes over orig_size ({orig_size}). '
            f'Could only trim {len(data) - len(result)} bytes from comments/whitespace.'
        )

    return bytes(result) + b'\x00' * (orig_size - len(result))


def fit_to_orig_size(plaintext: bytes, orig_size: int) -> bytes:
    """Normalize plaintext to exactly orig_size bytes."""
    if len(plaintext) > orig_size:
        return _shrink_to_orig_size(plaintext, orig_size)
    return _pad_to_orig_size(plaintext, orig_size)


def _make_css_incompressible(length: int) -> bytes:
    """Generate incompressible printable ASCII safe inside CSS /* */ comments."""
    # Printable ASCII (0x21-0x7E) excluding / and * (would close the comment)
    _ALPHABET = bytes(c for c in range(0x21, 0x7F) if c not in (ord('/'), ord('*')))
    rand = os.urandom(length)
    return bytes(_ALPHABET[b % len(_ALPHABET)] for b in rand)


def match_comp_size(plaintext_bytes: bytes, target: int) -> bytes:
    """LZ4-compress plaintext_bytes to exactly target compressed bytes.

    Fast path: if direct compress matches target, return immediately.
    Slow path: flatten all byte positions across ALL CSS comment bodies and
    binary-search how many to replace with incompressible os.urandom bytes.

    Using every byte position across all comments (not just ratio in the
    largest one) gives much finer-grained control over compressed size.
    Retries up to 8 times with fresh random bytes — LZ4 is not strictly
    monotonic, so a different fill gives a different curve that may reach
    previously-unreachable sizes. Mirrors the repacker's strategy.
    """
    fast = lz4.block.compress(plaintext_bytes, store_size=False)
    if len(fast) == target:
        return fast

    comments = _find_css_comments(plaintext_bytes)
    if not comments:
        raise RuntimeError(
            f'Cannot match comp_size={len(fast)} to target={target}: '
            'no CSS comments found for tuning.')

    # Flatten all byte positions across all comment bodies.
    # More positions = finer-grained compressed-size control.
    positions = [i for cstart, cend in comments for i in range(cstart, cend)]
    total = len(positions)

    # Baseline: all comment body bytes set to spaces (most compressible state).
    baseline = bytearray(plaintext_bytes)
    for pos in positions:
        baseline[pos] = 0x20
    baseline = bytes(baseline)

    c_lo = len(lz4.block.compress(baseline, store_size=False))
    if target < c_lo:
        raise RuntimeError(
            f'Could not match comp_size {target}: minimum reachable is {c_lo}. '
            'The CSS may have changed after a game update.')

    best_c_hi = 0

    def _try_fill(rand_fill: bytes) -> bytes | None:
        nonlocal best_c_hi

        def _build(n: int) -> bytes:
            trial = bytearray(baseline)
            for idx in range(n):
                trial[positions[idx]] = rand_fill[idx]
            return bytes(trial)

        c_all = len(lz4.block.compress(_build(total), store_size=False))
        best_c_hi = max(best_c_hi, c_all)
        if target > c_all:
            return None  # this fill can't reach high enough — retry

        lo, hi = 0, total
        while lo <= hi:
            mid = (lo + hi) // 2
            c_bytes = lz4.block.compress(_build(mid), store_size=False)
            c = len(c_bytes)
            if c == target:
                return c_bytes
            elif c < target:
                lo = mid + 1
            else:
                hi = mid - 1

        for n in range(max(0, lo - 50), min(lo + 50, total + 1)):
            c_bytes = lz4.block.compress(_build(n), store_size=False)
            if len(c_bytes) == target:
                return c_bytes

        return None

    for _ in range(8):
        result = _try_fill(_make_css_incompressible(total))
        if result is not None:
            return result

    if target > best_c_hi:
        raise RuntimeError(
            f'Could not match comp_size {target}: reachable range [{c_lo}, {best_c_hi}] '
            f'({len(comments)} comment(s), {total} positions). '
            'The CSS may have changed after a game update.')

    raise RuntimeError(
        f'Could not match comp_size {target} after 8 retries '
        f'(range [{c_lo}, {best_c_hi}], {total} tuning positions). Internal error.')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: patch_css_zip.py <path_to_mod.zip>')
        sys.exit(1)

    zip_path = Path(sys.argv[1])

    if not zip_path.exists():
        print(f'ERROR: File not found: {zip_path}')
        sys.exit(1)
    if not zipfile.is_zipfile(zip_path):
        print(f'ERROR: Not a valid zip file: {zip_path}')
        sys.exit(1)

    # ── Locate CSS inside zip ─────────────────────────────────────────
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            pamt_key, paz_key, _, css_offset, comp_size, orig_size = \
                _find_css_in_zip(zf)
            paz_bytes = zf.read(paz_key)
    except Exception as e:
        print(f'ERROR: Could not locate {CSS_FILENAME} in zip ({e}).')
        sys.exit(1)

    # detect top-level folder prefix (skip if numeric like 0036)
    parts = pamt_key.split('/')
    raw_prefix = (parts[0] + '/') if len(parts) > 2 else ''
    prefix = '' if (raw_prefix and parts[0].isdigit()) else raw_prefix

    # ── Extract and validate CSS ──────────────────────────────────────
    try:
        css_bytes = extract_css_from_bytes(paz_bytes, css_offset, comp_size, orig_size)
        css_bytes.decode('utf-8')
        if b'worldmapview' not in css_bytes and b'material-param' not in css_bytes:
            raise ValueError('CSS markers not found in decompressed data')
    except Exception as e:
        print(f'ERROR: Failed to read {CSS_FILENAME} from zip ({e}).')
        sys.exit(1)

    text = css_bytes.decode('utf-8')

    # ── Patch colors ──────────────────────────────────────────────────
    print('Applying dark mode...')
    colors = json.loads((HERE / 'colors.json').read_text(encoding='utf-8'))

    # Hardcoded: sea background normalization
    sea_vanilla = 'background-color: #FFF;}'
    sea_marker  = 'background-color: #FFFFFF;}'
    if sea_vanilla in text:
        text = text.replace(sea_vanilla, sea_marker, 1)
    elif sea_marker in text:
        print('  NOTE: sea marker already set to #FFFFFF')
    else:
        print('  WARNING: sea background marker not found')

    total = 0
    for entry in colors:
        preset = entry.get('preset')
        if preset:
            text, n = apply_color_scoped(text, preset, entry['vanilla'], entry['mod'])
        else:
            text, n = apply_color(text, entry['vanilla'], entry['mod'])
        total += n

    # ── Compress and write back into zip ──────────────────────────────
    encoded  = text.encode('utf-8')
    adjusted = fit_to_orig_size(encoded, orig_size)
    try:
        payload = match_comp_size(adjusted, comp_size)
    except Exception as e:
        print(f'ERROR: Compression tuning failed ({e}).')
        sys.exit(1)

    new_paz = patch_paz_bytes(paz_bytes, css_offset, comp_size, payload)

    try:
        if prefix:
            new_prefix = 'DarkMode_' + prefix
            rewrite_zip_entry(zip_path, paz_key, new_paz, rename_prefix=(prefix, new_prefix))
            print(f'Internal folder: {new_prefix.rstrip("/")}')
        else:
            rewrite_zip_entry(zip_path, paz_key, new_paz)
    except Exception as e:
        print(f'ERROR: Failed to write zip ({e}).')
        sys.exit(1)

    new_zip_path = zip_path.parent / f'DarkMode_{zip_path.name}'
    zip_path.rename(new_zip_path)
    print(f'Done. Dark mode applied ({total} color replacements).')
    print(f'Renamed to: {new_zip_path.name}')
