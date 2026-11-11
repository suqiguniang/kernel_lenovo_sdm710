#!/usr/bin/env python3
"""Android boot image (header v0/v1/v2) unpack/repack, no external deps.

用法:
  python3 bootimg.py info   stock-boot.img
  python3 bootimg.py unpack stock-boot.img outdir
  python3 bootimg.py repack-from-payload 出.img  内核Image.gz  \
        --payload stock-payload.bin --meta bootmeta.json --pad 67108864


Layout (all fields little-endian):
  magic[8] kernel_size ramdisk_size second_size
  kernel_addr ramdisk_addr second_addr tags_addr page_size
  header_version os_version[4;10+10+7+7] name[16] cmdline[512] id[8] extra_cmdline[1024]
  (v1+) recovery_dtbo_size recovery_dtbo_offset header_size
  (v2+) dtb_size dtb_addr
"""
import struct, sys, os, hashlib, gzip, io

HDR_V0 = struct.Struct('<8s10I')
HDR_OS = struct.Struct('<I')
HDR_NAME = struct.Struct('<16s')
HDR_CMDLINE = struct.Struct('<512s')
HDR_ID = struct.Struct('<32s')   # uint32_t id[8]
HDR_EXTRA = struct.Struct('<1024s')
V1 = struct.Struct('<IQI')          # recovery_dtbo_size, offset, header_size
V2 = struct.Struct('<IQ')           # dtb_size, dtb_addr

def parse(path):
    d = open(path, 'rb').read()
    magic, ksz, rsz, ssz, kaddr, raddr, saddr, tags, pagesz, ver, osver = HDR_V0.unpack_from(d, 0)
    assert magic == b'ANDROID!', magic
    off = HDR_V0.size
    name, = HDR_NAME.unpack_from(d, off); off += HDR_NAME.size
    cmdline, = HDR_CMDLINE.unpack_from(d, off); off += HDR_CMDLINE.size
    img_id, = HDR_ID.unpack_from(d, off); off += HDR_ID.size
    extra, = HDR_EXTRA.unpack_from(d, off); off += HDR_EXTRA.size
    info = dict(path=path, size=len(d), magic=magic.decode(), kernel_size=ksz, ramdisk_size=rsz,
                second_size=ssz, kernel_addr=kaddr, ramdisk_addr=raddr, second_addr=saddr,
                tags_addr=tags, page_size=pagesz, header_version=ver, os_version=osver,
                name=name.rstrip(b'\0').decode('utf-8', 'replace'),
                cmdline=cmdline.rstrip(b'\0').decode('utf-8', 'replace'),
                id=img_id.hex(), extra_cmdline=extra.rstrip(b'\0').decode('utf-8', 'replace'))
    if ver >= 1:
        rsz_dtbo, rdtbo_off, hdr_size = V1.unpack_from(d, off); off += V1.size
        info.update(recovery_dtbo_size=rsz_dtbo, recovery_dtbo_offset=rdtbo_off, header_size=hdr_size)
    else:
        info.update(header_size=pagesz if False else (HDR_V0.size + HDR_NAME.size + HDR_CMDLINE.size + HDR_ID.size + HDR_EXTRA.size))
    if ver >= 2:
        dtb_size, dtb_addr = V2.unpack_from(d, off); off += V2.size
        info.update(dtb_size=dtb_size, dtb_addr=dtb_addr)
    else:
        info.update(dtb_size=0, dtb_addr=0)
    info['_header_fields_size'] = off
    # payload offsets, page aligned
    p = pagesz
    def align(x): return (x + pagesz - 1) // pagesz * pagesz
    info['kernel_off'] = p; p += align(ksz)
    info['ramdisk_off'] = p; p += align(rsz)
    if ssz: info['second_off'] = p; p += align(ssz)
    if ver >= 1 and info['recovery_dtbo_size']: info['dtbo_off'] = p; p += align(info['recovery_dtbo_size'])
    if ver >= 2 and info['dtb_size']: info['dtb_off'] = p; p += align(info['dtb_size'])
    info['used_bytes'] = p
    info['_data'] = d
    return info

def print_info(i):
    print(f"文件: {i['path']}  ({i['size']} bytes)")
    for k in ['magic','header_version','page_size','kernel_size','ramdisk_size','second_size',
              'dtb_size','recovery_dtbo_size','header_size','kernel_addr','ramdisk_addr',
              'second_addr','tags_addr','dtb_addr','os_version']:
        if k in i: print(f"  {k:20s} {hex(i[k]) if isinstance(i[k], int) and k.endswith('addr') else i[k]}")
    print(f"  name                {i['name']!r}")
    print(f"  cmdline             {i['cmdline']!r}")
    print(f"  extra_cmdline       {i['extra_cmdline']!r}")
    print(f"  id                  {i['id']}")
    print(f"  区段偏移: kernel={i['kernel_off']} ramdisk={i['ramdisk_off']}"
          + (f" dtb={i.get('dtb_off')}" if i.get('dtb_off') else "")
          + (f" dtbo={i.get('dtbo_off')}" if i.get('dtbo_off') else ""))
    print(f"  有效数据到 {i['used_bytes']} bytes")

def extract(path, outdir):
    i = parse(path); d = i['_data']; os.makedirs(outdir, exist_ok=True)
    parts = {}
    def cut(name, off, size):
        if size:
            b = d[off:off+size]; p = os.path.join(outdir, name)
            open(p, 'wb').write(b); parts[name] = p; print(f"  写出 {name}: {len(b)} bytes")
    cut('kernel', i['kernel_off'], i['kernel_size'])
    cut('ramdisk', i['ramdisk_off'], i['ramdisk_size'])
    if i['second_size']: cut('second', i['second_off'], i['second_size'])
    if i.get('recovery_dtbo_size'): cut('recovery_dtbo', i['dtbo_off'], i['recovery_dtbo_size'])
    if i.get('dtb_size'): cut('dtb', i['dtb_off'], i['dtb_size'])
    return i, parts


def repack(out, stock_path, kernel, cmdline=None, pad_to=None):
    """Repack header v0/v1/v2, replacing ONLY the kernel.

    除内核外的所有字节（ramdisk、second、对齐填充、dtb 容器）都从原镜像原样保留，
    避免拆解/重组时破坏 Qualcomm 特有的未文档化区段。
    """
    import hashlib
    i = parse(stock_path)
    d = i['_data']
    pagesz = i['page_size']
    ver = i['header_version']
    if ver > 2:
        raise SystemExit(f'header v{ver} 不支持')

    k = open(kernel, 'rb').read()
    cmdl = (cmdline if cmdline is not None else i['cmdline']).encode()
    assert len(cmdl) < 512, 'cmdline 超长'

    def align(x):
        return (x + pagesz - 1) // pagesz * pagesz

    hdr = HDR_V0.pack(b'ANDROID!', len(k), i['ramdisk_size'], i['second_size'],
                      i['kernel_addr'], i['ramdisk_addr'], i['second_addr'],
                      i['tags_addr'], pagesz, ver, i['os_version'])
    hdr += HDR_NAME.pack(i['name'].encode()[:16].ljust(16, b'\0'))
    hdr += HDR_CMDLINE.pack(cmdl.ljust(512, b'\0'))

    h = hashlib.sha1()
    h.update(k); h.update(struct.pack('<I', len(k)))
    for (off, size) in [(i['ramdisk_off'], i['ramdisk_size'])] + \
                       ([(i['second_off'], i['second_size'])] if i['second_size'] else []):
        blob = d[off:off+size]
        h.update(blob); h.update(struct.pack('<I', len(blob)))
    if i.get('dtb_size'):
        blob = d[i['dtb_off']:i['dtb_off']+i['dtb_size']]
        h.update(blob); h.update(struct.pack('<I', len(blob)))
    hdr += HDR_ID.pack(h.digest().ljust(32, b'\0'))
    hdr += HDR_EXTRA.pack(i['extra_cmdline'].encode()[:1024].ljust(1024, b'\0'))
    if ver >= 1:
        hdr += V1.pack(i.get('recovery_dtbo_size', 0), i.get('recovery_dtbo_offset', 0),
                       i.get('header_size') or 1660)
    if ver >= 2:
        hdr += V2.pack(i['dtb_size'], i['dtb_addr'])
    assert len(hdr) <= pagesz

    out_b = bytearray(b'\0' * pagesz)
    out_b[0:len(hdr)] = hdr
    out_b.extend(k)
    out_b.extend(b'\0' * (align(len(k)) - len(k)))
    # 原镜像从 ramdisk 起到有效数据末尾，原样照抄
    out_b.extend(d[i['ramdisk_off']:i['used_bytes']])
    if pad_to:
        if len(out_b) > pad_to:
            raise SystemExit(f'新镜像 {len(out_b)} 超过 {pad_to}')
        out_b.extend(b'\0' * (pad_to - len(out_b)))
    open(out, 'wb').write(bytes(out_b))
    print(f'写出 {out}: {len(out_b)} bytes (新 kernel={len(k)}, 原 kernel={i["kernel_size"]})')
    return out

def repack_from_payload(out, payload_path, meta_path, kernel, pad_to=None, cmdline=None):
    """Rebuild a boot image from a kernel-less payload dump + meta.json.

    payload.bin 是原镜像从 ramdisk 起始到有效数据末尾的逐字节拷贝，因此
    ramdisk / second / 未文档化的对齐区、dtb 容器全部原样保留，只有内核被替换。
    """
    import hashlib, json
    m = json.load(open(meta_path))
    payload = open(payload_path, 'rb').read()
    assert len(payload) == m['payload_size'], 'payload 大小不符'
    assert hashlib.sha256(payload).hexdigest() == m['payload_sha256'], 'payload 校验失败'

    pagesz = m['pagesize']; ver = m['header_version']
    k = open(kernel, 'rb').read()
    cmdl = (cmdline if cmdline is not None else m['cmdline']).encode()
    assert len(cmdl) < 512

    def align(x): return (x + pagesz - 1) // pagesz * pagesz

    hdr = HDR_V0.pack(b'ANDROID!', len(k), m['ramdisk_size'], m['second_size'],
                      m['kernel_addr'], m['ramdisk_addr'], m['second_addr'],
                      m['tags_addr'], pagesz, ver, m['os_version'])
    hdr += HDR_NAME.pack(m['name'].encode()[:16].ljust(16, b'\0'))
    hdr += HDR_CMDLINE.pack(cmdl.ljust(512, b'\0'))
    h = hashlib.sha1()
    h.update(k); h.update(struct.pack('<I', len(k)))
    for rel, size in [(0, m['ramdisk_size']),
                      (m['second_rel'], m['second_size']),
                      (m['dtb_rel'], m['dtb_size'])]:
        if not size: continue
        blob = payload[rel:rel+size]
        h.update(blob); h.update(struct.pack('<I', len(blob)))
    hdr += HDR_ID.pack(h.digest().ljust(32, b'\0'))
    hdr += HDR_EXTRA.pack(m['extra_cmdline'].encode()[:1024].ljust(1024, b'\0'))
    if ver >= 1:
        hdr += V1.pack(0, 0, m['header_size'])
    if ver >= 2:
        hdr += V2.pack(m['dtb_size'], m['dtb_addr'])
    assert len(hdr) <= pagesz

    out_b = bytearray(b'\0' * pagesz)
    out_b[0:len(hdr)] = hdr
    out_b.extend(k)
    out_b.extend(b'\0' * (align(len(k)) - len(k)))
    out_b.extend(payload)
    if pad_to:
        if len(out_b) > pad_to:
            raise SystemExit(f'镜像 {len(out_b)} 超过 {pad_to}')
        out_b.extend(b'\0' * (pad_to - len(out_b)))
    open(out, 'wb').write(bytes(out_b))
    print(f'写出 {out}: {len(out_b)} bytes (kernel={len(k)})')
    return out


def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('info'); p1.add_argument('image')
    p2 = sub.add_parser('unpack'); p2.add_argument('image'); p2.add_argument('outdir')
    p3 = sub.add_parser('repack-from-payload')
    p3.add_argument('out'); p3.add_argument('kernel')
    p3.add_argument('--payload', required=True); p3.add_argument('--meta', required=True)
    p3.add_argument('--pad', type=int, default=None); p3.add_argument('--cmdline', default=None)
    p4 = sub.add_parser('repack')
    p4.add_argument('out'); p4.add_argument('stock'); p4.add_argument('kernel')
    p4.add_argument('--pad', type=int, default=None); p4.add_argument('--cmdline', default=None)
    a = ap.parse_args()
    if a.cmd == 'info':
        print_info(parse(a.image))
    elif a.cmd == 'unpack':
        i, _ = extract(a.image, a.outdir); print_info(i)
    elif a.cmd == 'repack-from-payload':
        repack_from_payload(a.out, a.payload, a.meta, a.kernel, pad_to=a.pad, cmdline=a.cmdline)
    elif a.cmd == 'repack':
        repack(a.out, a.stock, a.kernel, pad_to=a.pad, cmdline=a.cmdline)

if __name__ == '__main__':
    _cli()
