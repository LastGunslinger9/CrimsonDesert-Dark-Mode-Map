"""
Dark Mode Map patcher

Usage:
    python patch_css_zip.py <path_to_mod.zip>
"""
import io, json, os, re, struct, sys, zipfile
import lz4.block
from pathlib import Path

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

# --- chacha20 crypto ---
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
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    basename = os.path.basename(filename).lower()
    seed = _hashlittle(basename.encode('utf-8'), _HASH_INITVAL)
    iv   = struct.pack('<I', seed) * 4
    key  = b''.join(struct.pack('<I', (seed ^ _IV_XOR) ^ d) for d in _XOR_DELTAS)
    return Cipher(algorithms.ChaCha20(key, iv), mode=None).encryptor().update(data)


def _find_css_entry_in_pamt(data: bytes) -> tuple[int, int, int, int]:
    # find css in pamt index
    off = 0
    off += 4
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
    nodes: dict[int, tuple[int, str]] = {}
    while off < node_start + node_size:
        rel    = off - node_start
        parent = struct.unpack_from('<I', data, off)[0]
        slen   = data[off + 4]
        name   = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
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
            return paz_index, paz_offset, comp_size, orig_size

    raise FileNotFoundError(f'{CSS_FILENAME} not found in PAMT index')


def extract_css_from_bytes(paz_bytes: bytes, offset: int, comp_size: int, orig_size: int) -> bytes:
    raw = paz_bytes[offset : offset + comp_size]
    return lz4.block.decompress(_chacha20_crypt(raw, CSS_FILENAME), uncompressed_size=orig_size)


def patch_paz_bytes(paz_bytes: bytes, offset: int, comp_size: int, compressed: bytes) -> bytes:
    # replace css bytes in paz
    if len(compressed) != comp_size:
        raise ValueError(f'payload is {len(compressed)} bytes, expected {comp_size}')
    payload = _chacha20_crypt(compressed, CSS_FILENAME)
    ba = bytearray(paz_bytes)
    ba[offset : offset + comp_size] = payload
    return bytes(ba)


def _find_css_in_zip(zf: zipfile.ZipFile) -> tuple[str, str, int, int, int, int]:
    # scan pamt files in zip for css
    for name in zf.namelist():
        if not name.endswith('.pamt'):
            continue
        pamt_dir = name.rsplit('/', 1)[0] + '/'
        try:
            paz_index, css_offset, comp_size, orig_size = \
                _find_css_entry_in_pamt(zf.read(name))
            paz_key = pamt_dir + f'{paz_index}.paz'
            if paz_key in zf.namelist():
                return name, paz_key, paz_index, css_offset, comp_size, orig_size
        except Exception:
            continue
    raise FileNotFoundError(f'{CSS_FILENAME} not found in any .pamt inside zip')


def rewrite_zip_entry(zip_path: Path, entry_name: str, new_data: bytes,
                      rename_prefix: tuple[str, str] | None = None) -> None:
    # rebuild zip with patched entry
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
    m = re.search(r'#[0-9a-fA-F]+', old_str)
    if m is None:
        raise ValueError(f'No hex color found in old_str: {old_str!r}')
    orig_hex = m.group(0)
    alpha    = orig_hex[7:] if len(orig_hex) > 7 else ''
    new_str  = old_str.replace(orig_hex, cur_rgb + alpha)
    if len(old_str) != len(new_str):
        raise ValueError(f'Length mismatch: {old_str!r} -> {new_str!r}')
    count = text.count(old_str)
    if count == 0:
        print(f'  WARNING: not found - {old_str.strip()[:60]}')
        return text, 0
    return text.replace(old_str, new_str), count


def apply_color_scoped(text: str, preset: str, old_str: str, cur_rgb: str) -> tuple[str, int]:
    m = re.search(r'#[0-9a-fA-F]+', old_str)
    if m is None:
        raise ValueError(f'No hex color found in old_str: {old_str!r}')
    orig_hex = m.group(0)
    alpha    = orig_hex[7:] if len(orig_hex) > 7 else ''
    new_str  = old_str.replace(orig_hex, cur_rgb + alpha)
    if len(old_str) != len(new_str):
        raise ValueError(f'Length mismatch: {old_str!r} -> {new_str!r}')
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


_PRINTABLE_FILL = bytes(c for c in range(0x21, 0x7F) if c != 0x2A)


def _rand_fill(n: int) -> bytes:
    import random
    rng = random.Random(0xC4FEB4BE)
    return bytes(rng.choice(_PRINTABLE_FILL) for _ in range(n))


def match_comp_size(plaintext_bytes: bytes, target: int) -> bytes:
    fast = lz4.block.compress(plaintext_bytes, store_size=False)
    if len(fast) == target:
        return fast

    dc      = DEAD_COMMENT.encode('utf-8')
    dc_pos  = plaintext_bytes.find(dc)
    if dc_pos == -1:
        raise RuntimeError(
            f'Cannot match comp_size: direct compress={len(fast)} vs target={target},'
            ' and DEAD_COMMENT not found for tuning.')

    body_start = dc_pos + 2
    body_end   = dc_pos + len(dc) - 2
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
            f'Could not match comp_size {target}: reachable range [{c_lo}, {c_hi}].')

    lo, hi = 0, body_len
    while lo <= hi:
        mid    = (lo + hi) // 2
        c_bytes = lz4.block.compress(_build(mid), store_size=False)
        n      = len(c_bytes)
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

    raise RuntimeError(f'Could not match comp_size {target} after binary search.')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: patch_css_zip.py <path_to_mod.zip>')
        sys.exit(1)

    zip_path     = Path(sys.argv[1])

    if not zip_path.exists():
        print(f'ERROR: File not found: {zip_path}')
        sys.exit(1)
    if not zipfile.is_zipfile(zip_path):
        print(f'ERROR: Not a valid zip file: {zip_path}')
        sys.exit(1)

    # locate css
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

    # extract css
    try:
        text = extract_css_from_bytes(paz_bytes, css_offset, comp_size, orig_size).decode('utf-8')
    except Exception as e:
        print(f'ERROR: Failed to read {CSS_FILENAME} from PAZ ({e}).')
        sys.exit(1)

    # patch colors
    print('Applying Dark Mode to CSS...')
    original_len = len(text)
    colors       = json.loads((HERE / 'colors.json').read_text(encoding='utf-8'))

    text, _ = apply_color(text, 'background-color: #FFF;}', '#000')

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
    if text.count(DEAD_COMMENT) != 1:
        raise AssertionError('Dead comment not found — CSS structure may have changed.')

    compressed  = match_comp_size(text.encode('utf-8'), comp_size)
    new_paz     = patch_paz_bytes(paz_bytes, css_offset, comp_size, compressed)
    if prefix:
        new_prefix = 'DarkMode_' + prefix
        rewrite_zip_entry(zip_path, paz_key, new_paz, rename_prefix=(prefix, new_prefix))
        print(f'Internal folder: {new_prefix.rstrip("/")}')
    else:
        rewrite_zip_entry(zip_path, paz_key, new_paz)

    new_zip_path = zip_path.parent / f'DarkMode_{zip_path.name}'
    zip_path.rename(new_zip_path)
    print(f'Done. Dark Mode applied ({total} color replacements).')
    print(f'Renamed to: {new_zip_path.name}')
