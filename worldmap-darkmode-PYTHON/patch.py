"""
Dark Mode Map: Standalone patcher

Usage:
    python patch.py <game_dir>            # install dark mode
    python patch.py <game_dir> --restore  # restore vanilla
"""
import ctypes, json, os, re, struct, sys
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


class _FILETIME(ctypes.Structure):
    _fields_ = [('lo', ctypes.c_uint32), ('hi', ctypes.c_uint32)]


def _search_pamt(pamt_path: Path) -> tuple[Path, int, int, int] | None:
    """Search one PAMT file for worldmapview.css. Returns (paz_path, offset, comp_size, orig_size) or None."""
    try:
        data = pamt_path.read_bytes()
    except OSError:
        return None
    paz_dir = str(pamt_path.parent)
    pamt_stem = pamt_path.stem  # e.g. '0'
    try:
        stem_num = int(pamt_stem)
    except ValueError:
        stem_num = 0

    try:
        return _parse_pamt_for_css(data, paz_dir, stem_num)
    except Exception:
        return None


def _find_css_entry(game_dir: Path) -> tuple[Path, int, int, int]:
    """Scan all PAMT files in game_dir and return (paz_path, offset, comp_size, orig_size) for worldmapview.css."""
    pamt_files = sorted(game_dir.glob('*/0.pamt'))
    if not pamt_files:
        raise FileNotFoundError(f'No PAMT files found under {game_dir}')

    for pamt_path in pamt_files:
        result = _search_pamt(pamt_path)
        if result is not None:
            return result

    raise FileNotFoundError(f'{CSS_FILENAME} not found in any PAMT under {game_dir}')


def _parse_pamt_for_css(data: bytes, paz_dir: str, stem_num: int) -> tuple[Path, int, int, int] | None:
    """Parse PAMT bytes and return CSS entry, or None if not found."""
    if len(data) < 8:
        return None

    off = 0
    off += 4  # magic
    if off + 4 > len(data): return None
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
            paz_num = stem_num + paz_index
            paz_file = Path(paz_dir) / f'{paz_num}.paz'
            return paz_file, paz_offset, comp_size, orig_size

    return None


def extract_vanilla(paz_path: Path, offset: int, comp_size: int, orig_size: int) -> bytes:
    """Read, decrypt, and decompress CSS from PAZ."""
    with open(paz_path, 'rb') as f:
        f.seek(offset)
        raw = f.read(comp_size)
    return lz4.block.decompress(_chacha20_crypt(raw, CSS_FILENAME), uncompressed_size=orig_size)


def _save_timestamps(path: Path):
    """Return callable that restores NTFS timestamps on Windows."""
    if sys.platform != 'win32':
        return lambda: None
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    h = kernel32.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80 | 0x02000000, None)
    if h == -1:
        return lambda: None
    ct, at, mt = _FILETIME(), _FILETIME(), _FILETIME()
    kernel32.GetFileTime(h, ctypes.byref(ct), ctypes.byref(at), ctypes.byref(mt))
    kernel32.CloseHandle(h)
    def restore():
        h2 = kernel32.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x80 | 0x02000000, None)
        if h2 != -1:
            kernel32.SetFileTime(h2, ctypes.byref(ct), ctypes.byref(at), ctypes.byref(mt))
            kernel32.CloseHandle(h2)
    return restore


def write_paz(paz_path: Path, offset: int, comp_size: int, compressed: bytes) -> None:
    """Encrypt-then-write compressed payload to PAZ at offset, preserving NTFS timestamps."""
    if len(compressed) != comp_size:
        raise ValueError(f'payload is {len(compressed)} bytes, expected {comp_size}')
    payload = _chacha20_crypt(compressed, CSS_FILENAME)
    restore_ts = _save_timestamps(paz_path)
    with open(paz_path, 'r+b') as f:
        f.seek(offset)
        f.write(payload)
    restore_ts()


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


# Printable fill for CSS comment body tuning. Excludes * to prevent premature comment close
_PRINTABLE_FILL = bytes(c for c in range(0x21, 0x7F) if c != 0x2A)  # 93 chars


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
    """Normalize plaintext to exactly orig_size bytes like the full repacker flow."""
    if len(plaintext) > orig_size:
        return _shrink_to_orig_size(plaintext, orig_size)
    return _pad_to_orig_size(plaintext, orig_size)


def _rand_fill(n: int) -> bytes:
    import random
    rng = random.Random(0xC4FEB4BE)
    return bytes(rng.choice(_PRINTABLE_FILL) for _ in range(n))


def match_comp_size(plaintext_bytes: bytes, target: int) -> bytes:
    """LZ4-compress plaintext_bytes to exactly target compressed bytes.

    Fast path: if direct compress matches target, return immediately.
    Slow path: pick the largest CSS comment and fill its body with a mix of
    pseudo-random printable ASCII (incompressible) and spaces (compressible)
    to tune the compressed size to exactly target.

    Binary-searches the fill ratio; retries with fresh random bytes if the
    first attempt misses (LZ4 is not strictly monotonic — a different fill
    gives a different compression curve that may hit previously-unreachable
    sizes). Falls back to a full linear scan on the last fill if all retries
    fail.
    """
    fast = lz4.block.compress(plaintext_bytes, store_size=False)
    if len(fast) == target:
        return fast

    comments = sorted(_find_css_comments(plaintext_bytes), key=lambda c: c[1] - c[0], reverse=True)
    if not comments:
        raise RuntimeError(
            f'Cannot match comp_size={len(fast)} to target={target}: '
            'no CSS comments found for tuning.')

    body_start, body_end = comments[0]
    body_len = body_end - body_start

    def _build(n_fill: int, fill: bytes) -> bytes:
        ba = bytearray(plaintext_bytes)
        for i in range(body_len):
            ba[body_start + i] = fill[i] if i < n_fill else 0x20
        return bytes(ba)

    # Check reachable range once to detect CSS changes (game update).
    fill0 = _rand_fill(body_len)
    c_lo = len(lz4.block.compress(_build(0, fill0), store_size=False))
    c_hi = len(lz4.block.compress(_build(body_len, fill0), store_size=False))

    if not (c_lo <= target <= c_hi):
        raise RuntimeError(
            f'Could not match comp_size {target}: reachable range [{c_lo}, {c_hi}] '
            f'(tuning comment at {body_start}..{body_end}, {body_len} bytes). '
            'The CSS may have changed after a game update.')

    # Retry up to 8 times with fresh random fills — LZ4 is not strictly
    # monotonic, so a different fill gives a different compression curve
    # that may hit sizes unreachable with the first fill.
    fill = fill0
    for _attempt in range(8):
        if _attempt > 0:
            fill = _rand_fill(body_len)
        lo, hi = 0, body_len
        while lo <= hi:
            mid = (lo + hi) // 2
            c_bytes = lz4.block.compress(_build(mid, fill), store_size=False)
            n = len(c_bytes)
            if n == target:
                return c_bytes
            elif n < target:
                lo = mid + 1
            else:
                hi = mid - 1
        for n in range(max(0, lo - 32), min(lo + 32, body_len + 1)):
            c_bytes = lz4.block.compress(_build(n, fill), store_size=False)
            if len(c_bytes) == target:
                return c_bytes

    # Last resort: full linear scan over the entire range with the final fill.
    for n in range(body_len + 1):
        c_bytes = lz4.block.compress(_build(n, fill), store_size=False)
        if len(c_bytes) == target:
            return c_bytes

    raise RuntimeError(
        f'Could not match comp_size {target} after 8 retries with fresh random '
        f'fills (range [{c_lo}, {c_hi}]). Internal error.')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: patch.py <game_dir> [--restore]')
        sys.exit(1)

    game_dir = Path(sys.argv[1])
    restore_mode = '--restore' in sys.argv

    # ── Locate CSS entry via PAMT index ───────────────────────────────
    try:
        paz_path, offset, comp_size, orig_size = _find_css_entry(game_dir)
    except Exception as e:
        print(f'ERROR: Could not locate {CSS_FILENAME} in PAMT index ({e}).')
        sys.exit(1)

    # ── Validate entry looks like CSS ─────────────────────────────────
    try:
        vanilla_bytes = extract_vanilla(paz_path, offset, comp_size, orig_size)
        vanilla_bytes.decode('utf-8')
        if b'worldmapview' not in vanilla_bytes and b'material-param' not in vanilla_bytes:
            raise ValueError('CSS markers not found in decompressed data')
    except Exception as e:
        print(f'ERROR: Failed to read {CSS_FILENAME} from PAZ ({e}).')
        print('Restore original game files via Steam (right-click game → Properties → Local Files → Verify), then run again.')
        sys.exit(1)

    # ── Auto-backup vanilla CSS ───────────────────────────────────────
    backup_dir = HERE / 'backup'
    backup_file = backup_dir / 'worldmapview.css'
    backup_dir.mkdir(exist_ok=True)
    if not backup_file.exists():
        backup_file.write_bytes(vanilla_bytes)
        print('Backed up vanilla CSS.')
    elif not restore_mode:
        # If the live bytes contain the hardcoded vanilla marker the CSS is unmodded
        # (e.g. after a game update). Refresh the backup automatically.
        if b'background-color: #FFF;}' in vanilla_bytes:
            backup_file.write_bytes(vanilla_bytes)
            print('Vanilla CSS detected in game files. Backup refreshed.')

    # ── Restore mode ─────────────────────────────────────────────────
    if restore_mode:
        print('Restoring vanilla CSS...')
        if not backup_file.exists():
            print('ERROR: No backup found. Run install first.')
            sys.exit(1)
        source = backup_file.read_bytes()
        try:
            source.decode('utf-8')
            if b'worldmapview' not in source and b'material-param' not in source:
                raise ValueError('CSS markers missing')
        except Exception as e:
            print(f'ERROR: Backup file appears corrupt ({e}). Delete backup/worldmapview.css and re-run install.')
            sys.exit(1)
        adjusted = fit_to_orig_size(source, orig_size)
        payload = match_comp_size(adjusted, comp_size)
        try:
            write_paz(paz_path, offset, comp_size, payload)
        except Exception as e:
            print(f'ERROR: Failed to write to PAZ ({e}).')
            sys.exit(1)
        print('Done. Vanilla restored.')
        sys.exit(0)

    # ── Patch mode ────────────────────────────────────────────────────
    print('Applying dark mode...')
    text = backup_file.read_bytes().decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')

    colors = json.loads((HERE / 'colors.json').read_text(encoding='utf-8'))

    # Hardcoded: sea background
    sea_vanilla = 'background-color: #FFF;}'
    sea_marker = 'background-color: #FFFFFF;}'
    if sea_vanilla in text:
        text = text.replace(sea_vanilla, sea_marker, 1)
    elif sea_marker in text:
        print('  NOTE: sea marker already set to #FFFFFF')
    else:
        print('  WARNING: sea background marker not found')

    # User-editable colors
    total = 0
    for entry in colors:
        preset = entry.get('preset')
        if preset:
            text, n = apply_color_scoped(text, preset, entry['vanilla'], entry['mod'])
        else:
            text, n = apply_color(text, entry['vanilla'], entry['mod'])
        total += n

    encoded = text.encode('utf-8')
    adjusted = fit_to_orig_size(encoded, orig_size)
    payload = match_comp_size(adjusted, comp_size)
    try:
        write_paz(paz_path, offset, comp_size, payload)
    except Exception as e:
        print(f'ERROR: Failed to write to PAZ ({e}).')
        sys.exit(1)
    print(f'Done. Dark mode applied ({total} color replacements).')

