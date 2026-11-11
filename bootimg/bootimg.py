#!/usr/bin/env python3
"""Parse / repack Android boot images (header v0 .. v2), stdlib only.

Why this exists: the kernel tree only emits Image / Image.gz / Image.gz-dtb.
A flashable boot.img is a container around that payload plus the OEM's own
header parameters (load addresses, cmdline, page size), which must not be
guessed. This tool therefore never invents a header: it starts from the stock
image's first page, rewrites only the fields that legitimately change
(kernel_size, id), and appends the stock appended-DTB payload untouched.

Commands
  info     <boot.img>
  extract  <boot.img> --out DIR
  pack     --template-header FILE --kernel Image.gz --out FILE
           [--append-dtb FILE] [--partition-size N | --no-pad]
  verify   <boot.img> --template-header FILE --expect-sha256 SHA256

Header layouts (AOSP bootimg.h):
  v0 : magic, kernel_size, kernel_addr, ramdisk_size, ramdisk_addr,
       second_size, second_addr, tags_addr, page_size, dt_size, unused,
       name[16], cmdline[512], id[32], extra_cmdline[1024]        (1632 B)
  v1+: os_version, header_version, dtb_size, dtb_addr             (1648 B)
"""
import argparse
import hashlib
import json
import os
import struct
import sys

MAGIC = b'ANDROID!'
AVB_MAGIC = b'AVBf'
PAGE_DEFAULT = 4096


def u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


def put_u32(buf, off, val):
    struct.pack_into('<I', buf, off, val)


def parse_header(data):
    if data[:8] != MAGIC:
        raise ValueError('not an Android boot image (bad ANDROID! magic)')
    h = {
        'magic': 'ANDROID!',
        'kernel_size': u32(data, 8),
        'kernel_addr': u32(data, 12),
        'ramdisk_size': u32(data, 16),
        'ramdisk_addr': u32(data, 20),
        'second_size': u32(data, 24),
        'second_addr': u32(data, 28),
        'tags_addr': u32(data, 32),
        'page_size': u32(data, 36),
        'dt_size': u32(data, 40),
        'unused': u32(data, 44),
        'name': data[48:64].split(b'\0')[0].decode('latin-1'),
        'cmdline': data[64:576].split(b'\0')[0].decode('latin-1'),
        'id': data[576:608].hex(),
        'extra_cmdline': data[608:1632].split(b'\0')[0].decode('latin-1'),
    }
    header_version = u32(data, 1636) if len(data) >= 1648 else 0
    h['os_version'] = u32(data, 1632) if len(data) >= 1648 else 0
    h['header_version'] = header_version
    if header_version >= 1:
        h['dtb_size'] = u32(data, 1640)
        h['dtb_addr'] = struct.unpack_from('<Q', data, 1644)[0]
    else:
        # bytes 1632.. are page padding for v0 and carry no fields
        h['dtb_size'] = 0
        h['dtb_addr'] = 0
    if h['page_size'] not in (2048, 4096, 8192, 16384, 32768, 65536):
        raise ValueError(f"unreasonable page_size {h['page_size']}")
    return h


def parse_avb_footer(data):
    """AVB footer lives in the last 64 bytes of the partition; fields are BE."""
    foot = data[-64:]
    if foot[:4] != AVB_MAGIC:
        return None
    major, minor = struct.unpack_from('>II', foot, 4)
    orig_size, vbmeta_off, vbmeta_size = struct.unpack_from('>QQQ', foot, 12)
    return {
        'version': f'{major}.{minor}',
        'original_image_size': orig_size,
        'vbmeta_offset': vbmeta_off,
        'vbmeta_size': vbmeta_size,
    }


def parse_vbmeta(data, off, size):
    """Minimal AVB0 header read: enough to state whether it is signed."""
    v = data[off:off + max(size, 256)]
    if v[:4] != b'AVB0':
        return None
    (magic, vmaj, vmin, auth_sz, aux_sz, alg) = struct.unpack_from('>4sIIQQI', v, 0)
    # AvbVBMetaImageHeader: algorithm_type ends at 32, then the four block offsets
    (hash_off, hash_sz, sig_off, sig_sz) = struct.unpack_from('>QQQQ', v, 32)
    (pk_off, pk_sz) = struct.unpack_from('>QQ', v, 64)
    release = v[128:176].split(b'\0')[0].decode('latin-1')
    return {
        'version': f'{vmaj}.{vmin}',
        'algorithm': alg,
        'authentication_block_size': auth_sz,
        'signature_size': sig_sz,
        'public_key_size': pk_sz,
        'signed': auth_sz > 0 and sig_sz > 0,
        'release_string': release,
    }


def split_payload(kernel):
    """Split an Image.gz-dtb payload into (gzip stream, appended DTBs)."""
    import zlib
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        d.decompress(kernel)
    except zlib.error as exc:
        raise ValueError(f'kernel payload is not gzip: {exc}')
    used = len(kernel) - len(d.unused_data)
    return kernel[:used], kernel[used:]


def compute_id(kernel, ramdisk=b'', second=b''):
    """AOSP mkbootimg legacy SHA1 id."""
    sha = hashlib.sha1()
    for blob in (kernel, ramdisk, second):
        sha.update(blob)
        sha.update(struct.pack('<I', len(blob)))
    return sha.digest()


def pad_to(buf, page):
    rem = len(buf) % page
    return buf if rem == 0 else buf + b'\0' * (page - rem)


def cmd_info(args):
    data = open(args.image, 'rb').read()
    h = parse_header(data)
    print(json.dumps({'file': args.image, 'bytes': len(data), 'header': h}, indent=2))
    off = h['page_size']
    kernel = data[off:off + h['kernel_size']]
    print(f'kernel payload: {len(kernel)} B at {off:#x}')
    if kernel[:2] == b'\x1f\x8b':
        gz, dtb = split_payload(kernel)
        print(f'  gzip stream : {len(gz)} B')
        print(f'  appended DTB: {len(dtb)} B  (first magic {dtb[:4].hex()})')
    else:
        print(f'  raw arm64 Image (magic {kernel[0x38:0x40]!r})')
    foot = parse_avb_footer(data)
    print('avb footer:', foot)
    if foot:
        vb = parse_vbmeta(data, foot['vbmeta_offset'], foot['vbmeta_size'])
        print('avb vbmeta:', vb)
    return 0


def cmd_extract(args):
    data = open(args.image, 'rb').read()
    h = parse_header(data)
    page = h['page_size']
    os.makedirs(args.out, exist_ok=True)
    kernel = data[page:page + h['kernel_size']]
    open(os.path.join(args.out, 'header-page.bin'), 'wb').write(data[:page])
    open(os.path.join(args.out, 'kernel.bin'), 'wb').write(kernel)
    if kernel[:2] == b'\x1f\x8b':
        gz, dtb = split_payload(kernel)
        open(os.path.join(args.out, 'kernel.gz'), 'wb').write(gz)
        open(os.path.join(args.out, 'appended-dtb.bin'), 'wb').write(dtb)
    meta = {
        'source': os.path.basename(args.image),
        'source_bytes': len(data),
        'source_sha256': hashlib.sha256(data).hexdigest(),
        'header': h,
        'kernel_payload_sha256': hashlib.sha256(kernel).hexdigest(),
        'avb_footer': parse_avb_footer(data),
    }
    foot = meta['avb_footer']
    if foot:
        meta['avb_vbmeta'] = parse_vbmeta(data, foot['vbmeta_offset'], foot['vbmeta_size'])
    open(os.path.join(args.out, 'bootmeta.json'), 'w').write(json.dumps(meta, indent=2) + '\n')
    print(json.dumps(meta, indent=2))
    return 0


def build_image(template_header, kernel, append_dtb=b'', partition_size=None, page=None):
    """Return packed boot.img bytes; only kernel_size and id differ from stock."""
    h = parse_header(template_header.ljust(1632, b'\0'))
    page = page or h['page_size']
    if len(template_header) < page:
        raise ValueError(f'template header is {len(template_header)} B, need >= {page}')
    hdr = bytearray(template_header[:page])

    payload = kernel + append_dtb
    put_u32(hdr, 8, len(payload))
    put_u32(hdr, 16, h['ramdisk_size'])
    put_u32(hdr, 24, h['second_size'])
    if h['header_version'] >= 1:
        put_u32(hdr, 1640, h['dtb_size'])
    hdr[576:608] = compute_id(payload).ljust(32, b'\0')

    out = bytes(hdr) + pad_to(payload, page)
    if partition_size:
        if len(out) > partition_size:
            raise ValueError(f'image {len(out)} B exceeds partition {partition_size} B')
        out = out + b'\0' * (partition_size - len(out))
    return out


def cmd_pack(args):
    template = open(args.template_header, 'rb').read()
    kernel = open(args.kernel, 'rb').read()
    dtb = open(args.append_dtb, 'rb').read() if args.append_dtb else b''
    if kernel[:2] != b'\x1f\x8b' and not args.allow_raw:
        raise SystemExit(f'{args.kernel} is not gzip-compressed; pass --allow-raw to pack a raw Image')
    out = build_image(template, kernel, dtb,
                      None if args.no_pad else args.partition_size)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, 'wb').write(out)
    print(f'wrote {args.out}: {len(out)} B '
          f'(header {len(template[:parse_header(template)["page_size"]])+0} B page, '
          f'kernel {len(kernel)} B, appended dtb {len(dtb)} B)')
    return 0


def cmd_verify(args):
    data = open(args.image, 'rb').read()
    template = open(args.template_header, 'rb').read()
    h_in = parse_header(template.ljust(1648, b'\0'))
    h_out = parse_header(data)
    problems = []
    page = h_in['page_size']
    for key in ('kernel_addr', 'ramdisk_addr', 'second_addr', 'tags_addr',
                'page_size', 'ramdisk_size', 'second_size', 'dt_size',
                'cmdline', 'name', 'extra_cmdline', 'header_version'):
        if h_in[key] != h_out[key]:
            problems.append(f'{key}: template {h_in[key]!r} != image {h_out[key]!r}')
    if data[:page] != template[:page]:
        # the only allowed delta inside the first page is kernel_size + id
        allowed = bytearray(template[:page])
        put_u32(allowed, 8, h_out['kernel_size'])
        allowed[576:608] = data[576:608]
        if bytes(allowed) != data[:page]:
            problems.append('first page differs from template beyond kernel_size/id')
    if args.partition_size and len(data) != args.partition_size:
        problems.append(f'size {len(data)} != partition {args.partition_size}')
    tail_ok = True
    if args.expect_dtb_sha256:
        kernel = data[page:page + h_out['kernel_size']]
        _, dtb = split_payload(kernel)
        got = hashlib.sha256(dtb).hexdigest()
        if got != args.expect_dtb_sha256:
            problems.append(f'appended dtb sha256 {got} != expected {args.expect_dtb_sha256}')
            tail_ok = False
    if args.expect_sha256:
        got = hashlib.sha256(data).hexdigest()
        if got != args.expect_sha256:
            problems.append(f'sha256 {got} != expected {args.expect_sha256}')
    print(json.dumps({'image': args.image, 'bytes': len(data),
                      'kernel_size': h_out['kernel_size'],
                      'id': h_out['id'],
                      'appended_dtb_match': tail_ok,
                      'problems': problems}, indent=2))
    return 1 if problems else 0



def sha256(data):
    return hashlib.sha256(data).hexdigest()


def id_zeroed(img, page):
    """Digest of the image with the legacy id field blanked.

    The id at header offset 576 is a build-time SHA1 that no bootloader
    verifies, and the OEM's own convention could not be reproduced from the
    artefacts. Blanking it makes the round-trip assertion exact instead of
    fuzzy: everything else - header params, padding, payload - must match.
    """
    b = bytearray(img)
    b[576:608] = b'\0' * 32
    return sha256(bytes(b))


def cmd_import_stock(args):
    data = open(args.image, 'rb').read()
    h = parse_header(data)
    page = h['page_size']
    kernel = data[page:page + h['kernel_size']]
    gz, dtb = split_payload(kernel)
    foot = parse_avb_footer(data)
    meta = {
        'note': 'Generated by bootimg/bootimg.py import-stock. Do not hand-edit.',
        'source_name': os.path.basename(args.image),
        'source_bytes': len(data),
        'source_sha256': sha256(data),
        'partition_size': len(data),
        'header': h,
        'kernel_payload_bytes': len(kernel),
        'kernel_payload_sha256': sha256(kernel),
        'kernel_gz_bytes': len(gz),
        'kernel_gz_sha256': sha256(gz),
        'appended_dtb_bytes': len(dtb),
        'appended_dtb_sha256': sha256(dtb),
        'dtb_count': count_dtbs(dtb),
        'avb_footer': foot,
        'avb_vbmeta': parse_vbmeta(data, foot['vbmeta_offset'], foot['vbmeta_size']) if foot else None,
        'stock_prefix_sha256': sha256(data[:foot['original_image_size']]) if foot else sha256(data),
        'stock_prefix_sha256_id_zeroed':
            id_zeroed(data[:foot['original_image_size']], page) if foot else id_zeroed(data, page),
    }
    os.makedirs(args.out, exist_ok=True)
    open(os.path.join(args.out, 'header-page.bin'), 'wb').write(data[:page])
    open(os.path.join(args.out, 'stock-kernel.bin'), 'wb').write(kernel)
    open(os.path.join(args.out, 'appended-dtb.bin'), 'wb').write(dtb)
    open(os.path.join(args.out, 'bootmeta.json'), 'w').write(json.dumps(meta, indent=2) + '\n')
    print(json.dumps(meta, indent=2))
    return 0


def count_dtbs(dtb):
    """Count FDT headers (big-endian total_size == 0xd00dfeed magic walk)."""
    n = 0
    off = 0
    while off + 8 <= len(dtb):
        if dtb[off:off + 4] != b'\xd0\x0d\xfe\xed':
            break
        size = struct.unpack_from('>I', dtb, off + 4)[0]
        if size <= 0 or off + size > len(dtb):
            break
        n += 1
        off += size
    return n


def cmd_selftest(args):
    base = args.assets
    meta = json.load(open(os.path.join(base, 'bootmeta.json')))
    template = open(os.path.join(base, 'header-page.bin'), 'rb').read()
    kernel = open(os.path.join(base, 'stock-kernel.bin'), 'rb').read()
    gz, dtb = split_payload(kernel)
    rt = build_image(template, gz, dtb, None)
    problems = []
    got = id_zeroed(rt, page_of(template))
    if got != meta['stock_prefix_sha256_id_zeroed']:
        problems.append(f'round-trip id-zeroed sha256 {got} != {meta["stock_prefix_sha256_id_zeroed"]}')
    if len(rt) != meta['avb_footer']['original_image_size']:
        problems.append(f'round-trip size {len(rt)} != stock image size {meta["avb_footer"]["original_image_size"]}')
    if sha256(dtb) != meta['appended_dtb_sha256']:
        problems.append('appended dtb sha256 mismatch')
    print(json.dumps({'roundtrip_bytes': len(rt),
                      'roundtrip_sha256_id_zeroed': got,
                      'expected': meta['stock_prefix_sha256_id_zeroed'],
                      'dtb_count': count_dtbs(dtb),
                      'problems': problems}, indent=2))
    return 1 if problems else 0


def page_of(template):
    return parse_header(template.ljust(1632, b'\0'))['page_size']

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info'); p.add_argument('image'); p.set_defaults(func=cmd_info)

    p = sub.add_parser('extract')
    p.add_argument('image'); p.add_argument('--out', required=True); p.set_defaults(func=cmd_extract)

    p = sub.add_parser('pack')
    p.add_argument('--template-header', required=True)
    p.add_argument('--kernel', required=True)
    p.add_argument('--append-dtb')
    p.add_argument('--out', required=True)
    p.add_argument('--partition-size', type=int, default=67108864)
    p.add_argument('--no-pad', action='store_true')
    p.add_argument('--allow-raw', action='store_true')
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser('import-stock')
    p.add_argument('image'); p.add_argument('--out', required=True)
    p.set_defaults(func=cmd_import_stock)

    p = sub.add_parser('selftest')
    p.add_argument('--assets', default='bootimg')
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser('verify')
    p.add_argument('image')
    p.add_argument('--template-header', required=True)
    p.add_argument('--partition-size', type=int)
    p.add_argument('--expect-sha256')
    p.add_argument('--expect-dtb-sha256')
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
