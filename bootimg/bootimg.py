#!/usr/bin/env python3
"""kunlun2 (Lenovo Z6 Youth / Z6 Lite, L38111) boot.img packer -- Android 9 (Pie) stock layout.

No external dependencies: pure Python 3, no avbtool / mkbootimg / unpack_bootimg needed.

子命令
------
  info   <img>                      打印 header 与 AVB 结构（字段按 AOSP boot_img_hdr 正确顺序解析）
  unpack <img> <outdir>             拆出 kernel / dtb / vbmeta
  pack-pie <out> <kernel> [--meta]  用原厂镜像的参数封装新内核，并重算 AVB hash descriptor
  verify <img> [--meta]             逐项断言：header 字段、AVB footer、AVB 摘要与字节长度

原厂 Android 9 boot.img 的真实结构（由本工具从用户提供的原镜像逐字节确认）
----------------------------------------------------------------------
  [0 .. 4096)                  header page：ANDROID! + header_version=1
  [4096 .. +kernel_size)       Image.gz + 追加的 DTB 表（首个 DTB magic 0xD00DFEED）
  pad -> page_size             这里等于 vbmeta_offset，也等于 AVB 的 original_image_size
  [vbmeta_offset .. +512)      追加的 vbmeta（algorithm NONE，含 partition "boot" 的 hash descriptor）
  zeros ...
  [partition_size-64 .. )      AVB footer（AVBf 1.0 -> original_image_size / vbmeta_offset / vbmeta_size）

关键事实：ramdisk_size = second_size = dtb_size = 0，该机型 Pie 的 boot 分区里**没有 ramdisk**，
内核以 Image.gz + 追加 DTB 的形式被 bootloader 加载。因此重打包只需替换内核段，并把
AVB 尾部（vbmeta + footer）按新长度重新算出来；其余 header 参数一律沿用原厂值。
"""
import argparse
import hashlib
import json
import os
import struct
import sys

# --- AOSP boot_img_hdr -------------------------------------------------------
# magic[8] kernel_size kernel_addr ramdisk_size ramdisk_addr second_size
# second_addr tags_addr page_size header_version os_version
HDR_MAIN = struct.Struct('<8s10I')
NAME_FIELD = 16
CMDLINE_FIELD = 512
ID_FIELD = 32
EXTRA_CMDLINE_FIELD = 1024
HEADER_V1_SIZE = 1648          # AOSP: header v1 的 header_size 字段值
V1_FIELDS = struct.Struct('<IQI')   # recovery_dtbo_size, recovery_dtbo_offset, header_size

# --- AVB ---------------------------------------------------------------------
AVB_HEADER_SIZE = 256
AVB_MAGIC = b'AVB0'
AVB_FOOTER_MAGIC = b'AVBf'
AVB_FOOTER_SIZE = 64
AVB_DESC_TAG_HASH = 2
# AvbHashDescriptor 固定头：image_size(8) algorithm[32] name_len(4) salt_len(4)
#                          digest_len(4) flags(4) reserved[60]
AVB_HASH_DESC_SIZE = 8 + 32 + 4 + 4 + 4 + 4 + 60   # 116


def align(x, a):
    return (x + a - 1) // a * a


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------
def parse_header(data):
    magic, ksz, kaddr, rsz, raddr, ssz, saddr, tags, pagesz, ver, osver = \
        HDR_MAIN.unpack_from(data, 0)
    if magic != b'ANDROID!':
        raise SystemExit('不是 Android boot image（magic=%r）' % magic)
    h = dict(header_version=ver, os_version=osver, page_size=pagesz,
             kernel_size=ksz, kernel_addr=kaddr, ramdisk_size=rsz, ramdisk_addr=raddr,
             second_size=ssz, second_addr=saddr, tags_addr=tags)
    h['name'] = data[48:48 + NAME_FIELD].rstrip(b'\0').decode('utf-8', 'replace')
    h['cmdline'] = data[64:64 + CMDLINE_FIELD].rstrip(b'\0').decode('utf-8', 'replace')
    h['id'] = data[576:576 + ID_FIELD].hex()
    h['extra_cmdline'] = data[608:608 + EXTRA_CMDLINE_FIELD].rstrip(b'\0').decode('utf-8', 'replace')
    if ver >= 1:
        h['recovery_dtbo_size'], h['recovery_dtbo_offset'], h['header_size'] = \
            V1_FIELDS.unpack_from(data, 1632)
    else:
        h.update(recovery_dtbo_size=0, recovery_dtbo_offset=0,
                 header_size=1632)
    if ver >= 2:
        h['dtb_size'], h['dtb_addr'] = struct.unpack_from('<IQ', data, 1648)
    else:
        h.update(dtb_size=0, dtb_addr=0)
    # 段偏移
    p = pagesz
    h['kernel_off'] = p
    p += align(ksz, pagesz)
    h['ramdisk_off'] = p
    p += align(rsz, pagesz)
    if ssz:
        h['second_off'] = p
        p += align(ssz, pagesz)
    if h.get('recovery_dtbo_size'):
        h['dtbo_off'] = p
        p += align(h['recovery_dtbo_size'], pagesz)
    if h.get('dtb_size'):
        h['dtb_off'] = p
        p += align(h['dtb_size'], pagesz)
    h['used_bytes'] = p
    return h


def boot_id(kernel, ramdisk=b'', second=b''):
    """AOSP mkbootimg 的 id 算法：sha1(kernel||len||ramdisk||len||second||len)，取前 20 字节。"""
    h = hashlib.sha1()
    for blob in (kernel, ramdisk, second):
        h.update(blob)
        h.update(struct.pack('<I', len(blob)))
    return h.digest()[:20].ljust(ID_FIELD, b'\0')


def build_header(meta_header, kernel):
    hdr = dict(meta_header)
    page = hdr['page_size']
    ver = hdr['header_version']
    if len(hdr['cmdline'].encode()) >= CMDLINE_FIELD:
        raise SystemExit('cmdline 超过 512 字节')
    if len(hdr['name'].encode()) >= NAME_FIELD:
        raise SystemExit('name 超过 16 字节')
    out = HDR_MAIN.pack(b'ANDROID!', len(kernel), hdr['kernel_addr'],
                        hdr['ramdisk_size'], hdr['ramdisk_addr'],
                        hdr['second_size'], hdr['second_addr'],
                        hdr['tags_addr'], page, ver, hdr['os_version'])
    out += hdr['name'].encode().ljust(NAME_FIELD, b'\0')
    out += hdr['cmdline'].encode().ljust(CMDLINE_FIELD, b'\0')
    # 原厂 id 字段既不是 AOSP sha1(kernel||len)，也不是任何可复现的摘要；
    # 它在启动链里不被使用，因此沿用原值，让产物与原镜像的差异只落在内核段与 AVB 摘要上。
    if hdr.get('id'):
        raw = bytes.fromhex(hdr['id'])
        if len(raw) != ID_FIELD:
            raise SystemExit('meta.header.id 必须是 32 字节十六进制')
        out += raw
    else:
        out += boot_id(kernel)
    out += hdr['extra_cmdline'].encode().ljust(EXTRA_CMDLINE_FIELD, b'\0')
    if ver >= 1:
        out += V1_FIELDS.pack(hdr.get('recovery_dtbo_size', 0),
                              hdr.get('recovery_dtbo_offset', 0), HEADER_V1_SIZE)
    if ver >= 2:
        out += struct.pack('<IQ', hdr['dtb_size'], hdr['dtb_addr'])
    if len(out) > page:
        raise SystemExit('header %d 字节超过 page_size %d' % (len(out), page))
    return out


# ---------------------------------------------------------------------------
# AVB
# ---------------------------------------------------------------------------
def parse_vbmeta(vb):
    """解析 vbmeta 头与其中的第一个 hash descriptor。"""
    if vb[:4] != AVB_MAGIC:
        raise SystemExit('vbmeta magic 不是 AVB0: %r' % vb[:4])
    (vmaj, vmin) = struct.unpack_from('>II', vb, 4)
    auth_size, aux_size = struct.unpack_from('>QQ', vb, 12)
    algorithm = struct.unpack_from('>I', vb, 28)[0]
    desc_off, desc_size = struct.unpack_from('>QQ', vb, 96)
    aux = vb[AVB_HEADER_SIZE:AVB_HEADER_SIZE + aux_size]
    info = dict(version=[vmaj, vmin], auth_size=auth_size, aux_size=aux_size,
                algorithm=algorithm, descriptors_offset=desc_off,
                descriptors_size=desc_size)
    p = desc_off
    end = desc_off + desc_size
    while p + 16 <= end:
        tag, nbf = struct.unpack_from('>QQ', aux, p)
        if tag == AVB_DESC_TAG_HASH:
            body = aux[p + 16:p + 16 + nbf]
            image_size = struct.unpack_from('>Q', body, 0)[0]
            algo = body[8:40].rstrip(b'\0').decode('ascii', 'replace')
            name_len, salt_len, digest_len, flags = struct.unpack_from('>IIII', body, 40)
            q = AVB_HASH_DESC_SIZE
            part = body[q:q + name_len].decode('utf-8', 'replace'); q += name_len
            salt = body[q:q + salt_len]; q += salt_len
            digest = body[q:q + digest_len]
            info.update(tag='hash', image_size=image_size, hash_algorithm=algo,
                        partition=part, salt=salt, digest=digest,
                        digest_offset=AVB_HEADER_SIZE + p + 16 + AVB_HASH_DESC_SIZE + name_len + salt_len,
                        image_size_offset=AVB_HEADER_SIZE + p + 16)
            break
        p += 16 + nbf
    return info


def avb_expected_digest(image_bytes, salt):
    return hashlib.sha256(salt + image_bytes).digest()


def parse_footer(data):
    ft = data[-AVB_FOOTER_SIZE:]
    if ft[:4] != AVB_FOOTER_MAGIC:
        return None
    vmaj, vmin = struct.unpack_from('>II', ft, 4)
    orig, voff, vsize = struct.unpack_from('>QQQ', ft, 12)
    return dict(version=[vmaj, vmin], original_image_size=orig,
                vbmeta_offset=voff, vbmeta_size=vsize)


def build_footer(image_size, vbmeta_size):
    return (AVB_FOOTER_MAGIC + struct.pack('>II', 1, 0)
            + struct.pack('>QQQ', image_size, image_size, vbmeta_size)
            + b'\0' * 28)


# ---------------------------------------------------------------------------
# pack / verify
# ---------------------------------------------------------------------------
def load_meta(path):
    return json.load(open(path))


def pack_pie(out, kernel_path, meta_path, vbmeta_path, dtb_path=None):
    meta = load_meta(meta_path)
    kernel = open(kernel_path, 'rb').read()
    if dtb_path:
        kernel = kernel + open(dtb_path, 'rb').read()

    page = meta['header']['page_size']
    part = meta['partition']['size']
    vb_size = meta['avb']['vbmeta_size']

    hdr = build_header(meta['header'], kernel)
    img = bytearray(b'\0' * page)
    img[0:len(hdr)] = hdr
    img += kernel
    img += b'\0' * (align(len(img), page) - len(img))
    image_size = len(img)

    # AVB：沿用原厂 vbmeta，重算 image_size 与 hash descriptor 摘要
    vb = bytearray(open(vbmeta_path, 'rb').read())
    if len(vb) != vb_size:
        raise SystemExit('vbmeta 模板长度 %d != %d' % (len(vb), vb_size))
    info = parse_vbmeta(bytes(vb))
    struct.pack_into('>Q', vb, info['image_size_offset'], image_size)
    digest = avb_expected_digest(bytes(img), info['salt'])
    vb[info['digest_offset']:info['digest_offset'] + len(digest)] = digest

    img += vb
    if len(img) + AVB_FOOTER_SIZE > part:
        raise SystemExit('镜像 %d 字节超出分区 %d' % (len(img), part))
    img += b'\0' * (part - AVB_FOOTER_SIZE - len(img))
    img += build_footer(image_size, vb_size)
    open(out, 'wb').write(bytes(img))
    print('写出 %s: %d bytes' % (out, len(img)))
    print('  kernel      %d bytes (原厂 %d)' % (len(kernel), meta['stock']['kernel_size']))
    print('  image_size  %d (原厂 %d)' % (image_size, meta['stock']['image_end']))
    print('  avb digest  %s' % digest.hex())
    return out


def verify(img_path, meta_path=None):
    data = open(img_path, 'rb').read()
    h = parse_header(data)
    problems = []

    def check(cond, msg):
        print(('  ok   ' if cond else '  FAIL ') + msg)
        if not cond:
            problems.append(msg)

    print('文件 %s (%d bytes)' % (img_path, len(data)))
    check(h['header_version'] == 1, 'header_version == 1 (得到 %d)' % h['header_version'])
    check(h['page_size'] == 4096, 'page_size == 4096 (得到 %d)' % h['page_size'])
    check(h['ramdisk_size'] == 0, 'ramdisk_size == 0（Pie 原厂 boot 无 ramdisk）')
    check(h['second_size'] == 0, 'second_size == 0')
    check(h['dtb_size'] == 0, 'dtb_size == 0（v1 头，DTB 追加在 kernel 尾部）')
    check(h['header_size'] == HEADER_V1_SIZE, 'header_size == 1648')

    kern = data[h['kernel_off']:h['kernel_off'] + h['kernel_size']]
    check(kern[:2] == b'\x1f\x8b', 'kernel 以 gzip magic 开头')
    dtb_at = kern.find(b'\xd0\x0d\xfe\xed')
    check(dtb_at > 0, 'kernel 尾部含追加 DTB（offset %d）' % dtb_at)
    if dtb_at > 0:
        total = struct.unpack_from('>I', kern, dtb_at + 4)[0]
        check(total > 0 and dtb_at + total <= len(kern), '首个 DTB total_size 自洽 (%d)' % total)

    ft = parse_footer(data)
    check(ft is not None, '存在 AVB footer')
    if ft:
        check(ft['vbmeta_size'] == 512, 'footer.vbmeta_size == 512')
        check(ft['vbmeta_offset'] == ft['original_image_size'],
              'footer.vbmeta_offset == original_image_size (%d)' % ft['vbmeta_offset'])
        exp_end = align(h['kernel_off'] + h['kernel_size'], h['page_size'])
        check(ft['original_image_size'] == exp_end,
              'original_image_size(%d) == 内核段页对齐末尾(%d)' % (ft['original_image_size'], exp_end))
        vb = data[ft['vbmeta_offset']:ft['vbmeta_offset'] + ft['vbmeta_size']]
        info = parse_vbmeta(vb)
        check(info.get('algorithm') == 0, 'vbmeta algorithm == NONE(0)')
        check(info.get('partition') == 'boot', 'hash descriptor 针对分区 boot')
        check(info['image_size'] == ft['original_image_size'], 'descriptor.image_size 与 footer 一致')
        want = avb_expected_digest(data[:info['image_size']], info['salt'])
        check(want == info['digest'], 'AVB SHA256(salt||image) 与 descriptor 一致')
        print('  info  salt=%s' % info['salt'].hex())
        print('  info  digest=%s' % info['digest'].hex())

    if meta_path:
        meta = load_meta(meta_path)
        mh = meta['header']
        for key in ('kernel_addr', 'ramdisk_addr', 'second_addr', 'tags_addr', 'os_version'):
            check(h[key] == mh[key], '%s 沿用原厂值 %s' % (key, hex(mh[key])))
        check(h['cmdline'] == mh['cmdline'], 'cmdline 与原厂一致')
        check(len(data) == meta['partition']['size'],
              '镜像长度 == 分区长度 %d' % meta['partition']['size'])

    if problems:
        raise SystemExit('校验失败：%d 项' % len(problems))
    print('校验通过')
    return True


def cmd_info(path):
    data = open(path, 'rb').read()
    h = parse_header(data)
    print('文件 %s (%d bytes)' % (path, len(data)))
    for k in ('header_version', 'page_size', 'kernel_size', 'ramdisk_size', 'second_size',
              'dtb_size', 'header_size', 'os_version'):
        print('  %-16s %s' % (k, h[k]))
    for k in ('kernel_addr', 'ramdisk_addr', 'second_addr', 'tags_addr'):
        print('  %-16s %s' % (k, hex(h[k])))
    print('  %-16s %r' % ('name', h['name']))
    print('  %-16s %r' % ('cmdline', h['cmdline'][:100]))
    print('  %-16s %s' % ('id', h['id']))
    print('  段偏移: kernel=%d ramdisk=%d 有效数据到 %d' % (h['kernel_off'], h['ramdisk_off'], h['used_bytes']))
    ft = parse_footer(data)
    if ft:
        print('  AVB footer: version=%s original_image_size=%d vbmeta_offset=%d vbmeta_size=%d'
              % (ft['version'], ft['original_image_size'], ft['vbmeta_offset'], ft['vbmeta_size']))
        vb = data[ft['vbmeta_offset']:ft['vbmeta_offset'] + ft['vbmeta_size']]
        info = parse_vbmeta(vb)
        print('  AVB: algorithm=%d partition=%s image_size=%d digest=%s'
              % (info['algorithm'], info.get('partition'), info['image_size'],
                 info.get('digest', b'').hex()[:32] + '...'))
    else:
        print('  无 AVB footer')


def cmd_unpack(path, outdir):
    data = open(path, 'rb').read()
    h = parse_header(data)
    os.makedirs(outdir, exist_ok=True)
    kern = data[h['kernel_off']:h['kernel_off'] + h['kernel_size']]
    open(os.path.join(outdir, 'kernel.gz-dtb'), 'wb').write(kern)
    at = kern.find(b'\xd0\x0d\xfe\xed')
    if at > 0:
        open(os.path.join(outdir, 'dtb.bin'), 'wb').write(kern[at:])
        open(os.path.join(outdir, 'Image.gz'), 'wb').write(kern[:at])
    ft = parse_footer(data)
    if ft:
        open(os.path.join(outdir, 'vbmeta.img'), 'wb').write(
            data[ft['vbmeta_offset']:ft['vbmeta_offset'] + ft['vbmeta_size']])
        open(os.path.join(outdir, 'avb-footer.bin'), 'wb').write(data[-AVB_FOOTER_SIZE:])
    cmd_info(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description='kunlun2 Android 9 boot.img packer')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info'); p.add_argument('image')
    p = sub.add_parser('unpack'); p.add_argument('image'); p.add_argument('outdir')

    p = sub.add_parser('pack-pie')
    p.add_argument('out')
    p.add_argument('kernel', help='Image.gz（可再用 --dtb 追加 DTB）')
    p.add_argument('--meta', default=os.path.join(os.path.dirname(__file__), 'android9-bootmeta.json'))
    p.add_argument('--vbmeta', default=os.path.join(os.path.dirname(__file__), 'avb-vbmeta-template.bin'))
    p.add_argument('--dtb', default=None, help='追加 DTB 表（默认使用 --meta 同目录的 stock-dtb.bin）')

    p = sub.add_parser('verify')
    p.add_argument('image')
    p.add_argument('--meta', default=os.path.join(os.path.dirname(__file__), 'android9-bootmeta.json'))

    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    if a.cmd == 'info':
        cmd_info(a.image)
    elif a.cmd == 'unpack':
        cmd_unpack(a.image, a.outdir)
    elif a.cmd == 'pack-pie':
        dtb = a.dtb if a.dtb else (None if 'Image.gz-dtb' in os.path.basename(a.kernel)
                                   else os.path.join(here, 'stock-dtb.bin'))
        pack_pie(a.out, a.kernel, a.meta, a.vbmeta, dtb)
    elif a.cmd == 'verify':
        verify(a.image, a.meta)


if __name__ == '__main__':
    main()
