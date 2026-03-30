"""
Dark Mode Map standalone patcher for Crimson Desert.
Usage:
    python patch.py <game_dir>            # install dark mode
    python patch.py <game_dir> --restore  # restore vanilla
"""
import ctypes, json, os, re, struct, sys
import lz4.block
from pathlib import Path

# ---------------------------------------------------------------------------
# PAZ entry discovery via PAMT index (game-update-proof)
# ---------------------------------------------------------------------------
PAMT_SUBPATH = '0012/0.pamt'
CSS_FILENAME  = 'worldmapview.css'

DEAD_COMMENT = (
    '/* .worldmap-top-dimmed { opacity: 1; position: absolute; '
    'width: 100%; height: 100%; background-color: #1c1c1c; top: 0; '
    'left: 0; background-image: textureid(cd_common_decoline_pattern_diagonal_04); '
    'background-repeat: repeat; background-blend-mode: multiply; '
    'mask-gradient-direction: all; mask-gradient-start-opacity: 0; '
    'mask-gradient-end-opacity: 1; mask-gradient-width: 700px; '
    'background-size: 48.000000px auto; } */'
)

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# Inline ChaCha20 key derivation (mirrors paz_crypto.py, no external tools needed)
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


def _find_css_entry(game_dir: Path) -> tuple[Path, int, int, int]:
    """Parse PAMT index and return (paz_path, offset, comp_size, orig_size) for worldmapview.css."""
    pamt_path = game_dir / PAMT_SUBPATH.replace('/', '\\')
    if not pamt_path.exists():
        raise FileNotFoundError(f'PAMT not found: {pamt_path}')

    data = pamt_path.read_bytes()
    paz_dir = str(pamt_path.parent)

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
            paz_file = Path(paz_dir) / f'{paz_index}.paz'
            return paz_file, paz_offset, comp_size, orig_size

    raise FileNotFoundError(f'{CSS_FILENAME} not found in PAMT index: {pamt_path}')


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
    if len(old_str) != len(new_str):
        raise ValueError(f'Length mismatch {len(old_str)} vs {len(new_str)}: {old_str!r} -> {new_str!r}')
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
    if len(old_str) != len(new_str):
        raise ValueError(f'Length mismatch {len(old_str)} vs {len(new_str)}: {old_str!r} -> {new_str!r}')
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


# Deterministic printable fill for dead-comment body tuning (excludes * to keep comment valid)
_PRINTABLE_FILL = bytes(c for c in range(0x21, 0x7F) if c != 0x2A)  # 93 chars


def _rand_fill(n: int) -> bytes:
    """Return n deterministic pseudo-random bytes from _PRINTABLE_FILL."""
    import random
    rng = random.Random(0xC4FEB4BE)
    return bytes(rng.choice(_PRINTABLE_FILL) for _ in range(n))


def match_comp_size(plaintext_bytes: bytes, target: int) -> bytes:
    """LZ4-compress plaintext_bytes to exactly target compressed bytes.

    Fast path: if direct compress matches target, return immediately.
    Slow path: binary-search the leading bytes of the DEAD_COMMENT body,
    replacing them with deterministic pseudo-random printable ASCII
    (incompressible) vs spaces (compressible) to tune compressed size.
    """
    fast = lz4.block.compress(plaintext_bytes, store_size=False)
    if len(fast) == target:
        return fast

    dc = DEAD_COMMENT.encode('utf-8')
    dc_pos = plaintext_bytes.find(dc)
    if dc_pos == -1:
        raise RuntimeError(
            f'Cannot match comp_size: direct compress={len(fast)} vs target={target},'
            ' and DEAD_COMMENT not found for tuning.')

    body_start = dc_pos + 2           # skip '/*'
    body_end   = dc_pos + len(dc) - 2  # before '*/'
    body_len   = body_end - body_start
    fill       = _rand_fill(body_len)

    def _build(n_fill: int) -> bytes:
        ba = bytearray(plaintext_bytes)
        for i in range(body_len):
            ba[body_start + i] = fill[i] if i < n_fill else 0x20
        return bytes(ba)

    c_lo = len(lz4.block.compress(_build(0), store_size=False))
    c_hi = len(lz4.block.compress(_build(body_len), store_size=False))

    if not (c_lo <= target <= c_hi):
        raise RuntimeError(
            f'Could not match comp_size {target}: reachable range [{c_lo}, {c_hi}]. '
            'The CSS may have changed after a game update.')

    lo, hi = 0, body_len
    while lo <= hi:
        mid = (lo + hi) // 2
        c_bytes = lz4.block.compress(_build(mid), store_size=False)
        n = len(c_bytes)
        if n == target:
            return c_bytes
        elif n < target:
            lo = mid + 1
        else:
            hi = mid - 1

    for n in range(max(0, lo - 32), min(lo + 32, body_len + 1)):
        c_bytes = lz4.block.compress(_build(n), store_size=False)
        if len(c_bytes) == target:
            return c_bytes

    raise RuntimeError(
        f'Could not match comp_size {target} after binary search '
        f'(range [{c_lo}, {c_hi}]). Internal error.')


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
            print('Game update detected, backup refreshed.')

    # ── Restore mode ─────────────────────────────────────────────────
    if restore_mode:
        print('Restoring vanilla CSS...')
        source = backup_file.read_bytes()
        payload = match_comp_size(source, comp_size)
        write_paz(paz_path, offset, comp_size, payload)
        print('Done. Vanilla restored.')
        sys.exit(0)

    # ── Patch mode ────────────────────────────────────────────────────
    print('Applying dark mode...')
    text = backup_file.read_text(encoding='utf-8', newline='')
    original_len = len(text)

    colors = json.loads((HERE / 'colors.json').read_text(encoding='utf-8'))

    # Hardcoded: sea background
    text, _ = apply_color(text, 'background-color: #FFF;}', '#000')

    # User-editable colors
    total = 0
    for entry in colors:
        preset = entry.get('preset')
        if preset:
            text, n = apply_color_scoped(text, preset, entry['vanilla'], entry['mod'])
        else:
            text, n = apply_color(text, entry['vanilla'], entry['mod'])
        total += n

    if len(text) != original_len:
        raise AssertionError('File size changed after color replacements!')

    # Verify dead comment present (match_comp_size uses it for compressed-size tuning)
    if text.count(DEAD_COMMENT) != 1:
        raise AssertionError('Dead comment not found or duplicated, CSS may have changed after a game update')

    encoded = text.encode('utf-8')
    payload = match_comp_size(encoded, comp_size)
    write_paz(paz_path, offset, comp_size, payload)
    print(f'Done. Dark mode applied ({total} color replacements).')

