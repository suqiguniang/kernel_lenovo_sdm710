# kunlun2 (Lenovo Z6 Youth / Z6 Lite, L38111) — Android 9 (Pie) 内核 + Droidspaces v6.6.0

本分支 `droidspaces-android9` 的目标：在**原厂 Android 9 基线**上打开 Droidspaces v6.6.0 所需的
非 GKI 内核能力，并由 GitHub Actions 产出**可直接刷入**的 `boot.img`。

---

## 1. 基线是怎么定的（取证，不靠印象）

### 1.1 上游 `lenovo-sdm710/kernel_lenovo_sdm710` 没有 Android 9 分支

`git ls-remote --heads` 与 API 双重确认，该仓库只有 9 个分支：

| 分支 | tip | 内核 | 说明 |
|---|---|---|---|
| `eleven` | 7d70c8fa (2021-02-12) | 4.9.268 | 提交历史里有 `Merge branch 'a11/upstream-aosp' into a11/master` → Android 11 基线 |
| `twelve` / `twelve-ek` | 525d356e (2022-09-07) | 4.9.317 | `android-4.9-q` / `android12-4.9-sdm845` 合并 → Android 12 基线 |
| `twelve-old` | 7d70c8fa | 4.9.268 | 同 `eleven` |
| `thirteen` / `thirteen-dynamic` | ba6c748a / 02a7b65e | 4.9.273 | Android 13 基线 |
| `fourteen` / `lineage-21` | 43a7045f | 4.9.317 | Android 14 基线 |

即：上游最老的可用树是 `eleven`（Android 11 / 4.9.268），**不存在 Pie 分支**。

### 1.2 机主提供的原厂 boot.img 指向的内核是 4.9.112

对 `/tmp/.../boot.img`（sha256 `ed1d745e…`）的解包结果：

```
magic ANDROID!  header_version 1   page_size 4096   os_version 0x12000134 (= Android 9.0.0, patch 2019-04)
kernel_size 12588115   ramdisk_size 0   second_size 0   dtb_size 0
kernel_addr 0x8000   ramdisk_addr 0x1000000   second_addr 0xf00000   tags_addr 0x100
cmdline 'console=ttyMSM0,115200n8 … androidboot.configfs=true androidboot.usbcontroller=a600000.dwc3 … buildvariant=user'
kernel 段 = gzip(11,366,552 B) + 追加 DTB 表(1,221,563 B, 首个 DTB total 365,404)
镜像有效长度 12,595,200，其后为 AppendVBMeta(512 B) 与 AVBf footer（algorithm = NONE）
kernel 内嵌版本串：Linux version 4.9.112-perf+ (buildslave@byws28) … Fri Jun 7 19:29:42 CST 2019
```

**关键结论：这是 kernel-only boot 镜像** —— `ramdisk_size = 0`，原厂 Pie ROM 的 boot 分区里没有
ramdisk，内核以 `Image.gz + 追加 DTB` 直接被 bootloader 加载。因此重打包只需替换内核段，
不需要、也不可能"塞一个 ramdisk 进去"。

### 1.3 因此基线选 4.9.112 的 Pie 源码树

`https://github.com/RaySlash/kernel_lenovo_sdm710` 的 `pie` 分支：`Import Lenovo Updates`
（2019-11-17），`Makefile` = 4.9.112，`arch/arm64/configs/kunlun2_defconfig` 在树，DTB 有
`lenovo-kunlun2.dtsi` / `sdm710-kunlun2-fpc.dtsi` / `sdm670-camera-sensor-kunlun2.dtsi`。

选它的唯一理由：**vermagic 能对上原厂**。原厂 `/vendor/lib/modules/*.ko` 的 vermagic 是
`4.9.112-perf+`，只要内核版本与 `CONFIG_LOCALVERSION` 一起对上，原厂模块就能继续加载；
用 `eleven`（4.9.268）等分支不可能做到这一点。

---

## 2. Droidspaces v6.6.0 落地内容

- `arch/arm64/configs/droidspaces-fragment.config`
  Droidspaces-OSS v6.6.0（`cff50fa0`）`Documentation/Kernel-Configuration.md` 的 non-GKI 片段，
  **逐条按本树 Kconfig 核实过存在性**，并因 4.9 缺符号做了三处调整：
  - 去掉 `CONFIG_NETFILTER_XT_TARGET_REJECT`（4.9 无此符号；IPv4 的 reject 由 `IP_NF_TARGET_REJECT` 覆盖）
  - 无 `FW_LOADER_COMPRESS`（4.18+）、无 `NETFILTER_XT_TARGET_MASQUERADE` / `NF_CONNTRACK_NETLINK` / `IP_NF_TARGET_ULOG`
  - `IPC_NS` 由 `SYSVIPC` / `POSIX_MQUEUE` 自动 select，不再单列
- `arch/arm64/configs/kunlun2_defconfig`
  在原厂 full config 末尾追加上述片段（kconfig 末行覆盖前文），并额外的两处**兼容性覆盖**：

  | 覆盖 | 原厂值 | 本分支值 | 原因 |
  |---|---|---|---|
  | `CONFIG_LOCALVERSION` | `"-perf"` | `"-perf+"` | 原厂内核串是 `4.9.112-perf+`（`+` 来自脏树构建），vermagic 必须逐字一致，否则 `/vendor` 下所有 `.ko` 加载失败 |
  | `CONFIG_MODULE_SIG_FORCE` | `y` | `n` | 原厂 `.ko` 用 Lenovo 私钥签名，自编内核内嵌的是自己生成的公钥；强制校验会让原厂模块全部被拒 |

已核实（本地 `make kunlun2_defconfig` + `olddefconfig` 后逐项断言，33/33 全 `=y`）：
`SYSVIPC POSIX_MQUEUE IPC_NS UTS_NS PID_NS USER_NS SECCOMP SECCOMP_FILTER CGROUPS CGROUP_DEVICE
CGROUP_PIDS MEMCG CGROUP_SCHED FAIR_GROUP_SCHED CGROUP_FREEZER CGROUP_NET_PRIO DEVTMPFS OVERLAY_FS
TMPFS_POSIX_ACL TMPFS_XATTR NET_NS VETH BRIDGE NF_TABLES IP_NF_TARGET_MASQUERADE NF_NAT_REDIRECT
IP_ADVANCED_ROUTER IP_MULTIPLE_TABLES IP_SET NETFILTER_XT_SET IP_NF_TARGET_REJECT NF_NAT_IPV4 NF_CONNTRACK_IPV4`

---

## 3. boot.img 打包（`bootimg/bootimg.py`，纯 Python，无 avbtool 依赖）

### 3.1 产物结构 = 原厂结构

```
[0 .. 4096)              header page：ANDROID! / header v1 / 原厂 addr+cmdline+os_version 原样沿用
[4096 .. +kernel_size)   新内核 = 本分支 Image.gz + 原厂 DTB 表（bootimg/stock-dtb.bin）
pad -> page               即 vbmeta_offset == AVB original_image_size
[+0 .. +512)             AppendVBMeta：沿用原厂 vbmeta，重写 image_size 与该分区 hash descriptor 的摘要
zeros …
[partition-64 .. )       AVBf footer 1.0：original_image_size = vbmeta_offset = 新长度，vbmeta_size = 512
```

- `bootimg/android9-bootmeta.json`：原厂 header 参数 + AVB 参数 + 原厂 id 字段（Lenovo 的 id 既不是
  AOSP `sha1(kernel||len)` 也不可复现，bootloader 不使用，故原值沿用，保证差异最小）
- `bootimg/stock-dtb.bin`：原厂追加 DTB 表逐字节拷贝，DTB 不被重建（面板/传感器/引脚配置不冒险）
- `bootimg/avb-vbmeta-template.bin`：原厂 512 B AppendVBMeta（algorithm NONE，hash descriptor 针对 `boot`）

### 3.2 无损性证明（原厂件 round-trip）

用**原厂内核**走一遍打包流程，产物与原厂 boot.img **逐字节相同**：

```
$ python3 bootimg/bootimg.py pack-pie rt.img stock-Image.gz
$ cmp rt.img stock-boot.img && echo BYTE-IDENTICAL
ROUND-TRIP BYTE-IDENTICAL
```

也就是说：换内核后，除内核段与 AVB 摘要（以及因此变化的长度/偏移字段）之外，其余每个字节
——header 参数、cmdline、DTB 表、vbmeta 结构、footer ——都与原厂一致。

### 3.3 AVB

原厂 boot 的 AVB 是 `algorithm NONE` + AppendVBMeta：`digest = SHA256(salt ‖ image[0:image_size])`，
`image_size` = 内核段页对齐末尾。打包器按新长度重算 `image_size` 与 digest 并回写原 vbmeta blob，
footer 同步更新，因此产物自身是自洽的（`verify` 会重新算一遍断言）。
真机若开着 dm-verity/vbmeta 校验链，顶层 vbmeta 分区仍由 Lenovo 签名，仍需在 fastboot 侧
`--disable-verification --disable-verity` 或解锁状态下刷入。

---

## 4. 构建与产物

GitHub Actions（`.github/workflows/build-kernel.yml`）：`ubuntu:20.04` 容器 + `gcc-aarch64-linux-gnu 9.4`
（4.9 时代 vendor 代码的 `-Werror` 用 `KCFLAGS="-fno-PIE -Wno-error"` 处理），产物：

| 文件 | 说明 |
|---|---|
| `boot.img` | 可直接 `fastboot flash boot` |
| `Image.gz` / `Image.gz-dtb` | 裸内核（调试/其它打包器用） |
| `kunlun2-defconfig.resolved` | 实际生效的 `.config`（用于核对符号） |
| `modules-4.9.112-perf+.tar.gz` | best-effort 编译的本内核模块（原厂模块 CRC 不合时用来替换） |
| `SHA256SUMS.txt` | 全部产物校验和 |

---

## 5. 刷机与回滚

```bash
# 解锁 BL 后
fastboot flash boot boot.img
fastboot --disable-verification --disable-verity flash vbmeta vbmeta.img   # 视 ROM 是否校验而定，可省
fastboot reboot
# 回滚（务必先备份自己的原厂 boot）
fastboot flash boot stock-boot.img
```

真机侧核对（内核起来后）：

```bash
uname -r                       # 期望 4.9.112-perf+
zcat /proc/config.gz | grep -E 'USER_NS|NET_NS|OVERLAY_FS|CGROUP_PIDS|IP_SET'
ls /vendor/lib/modules | head  # 原厂模块是否仍能 insmod（vermagic 已对齐）
```

---

## 6. 已知限制（照实写）

1. **4.9.112 + 原厂 defconfig 的 config 差异**：打开 namespace/cgroup/netfilter 后，个别导出符号的
   CRC 可能变化，导致极少数原厂 `.ko` 报 `disagrees about version of symbol`。届时用产物里的
   `modules-*.tar.gz` 覆盖 `/vendor/lib/modules`（需 root）。
2. **CI 绿 ≠ 可刷机**：内核能否启动只能在真机上验证；本分支的所有断言只覆盖"配置符号齐全、
   boot.img 结构自洽、AVB 摘要自洽、与原厂差异最小"。
3. 原厂 boot 没有 ramdisk，本打包器也不生成；若某天刷了需要 boot ramdisk 的 ROM（非本机 Pie 原厂），
   本产物不适用。
4. GCC 9 编译 2019 年的 vendor 代码，告警被 `-Wno-error` 压掉，不影响功能但可能掩盖潜在问题。
