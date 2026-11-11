# 内核集成说明 — `droidspaces-twelve` 分支

Lenovo Z6 Youth (L38111 / kunlun2) 内核 4.9.317 的变体集成记录。

| 项 | 值 |
|---|---|
| 设备/平台 | Lenovo Z6 Youth (L38111)，Qualcomm sdm710 (sdm670)，`kunlun2_defconfig` |
| 内核版本 | 4.9.317 |
| 分支 | `droidspaces-twelve` |
| 基线提交 | `525d356eea2a4d6031e271b99849b066ac30c4ca`（origin/twelve，Android 12 / LOS 19 线） |
| 起步方式 | 本地 `twelve-integration` 保留真实谱系，远端推无父快照 |
| 目标 ROM | LineageOS 19 / Android 12 |

## 组件版本（按 commit 锚定，非 tag 猜测）

- **Droidspaces v6.6.0**
  - 来源：`ravindu644/Droidspaces-OSS`
  - commit：`cff50fa04d50472b607ba6822e8bf482b19cd427`（tag `v6.6.0`，2026-09-21）
  - 参考文档：`Documentation/Kernel-Configuration.md` → “Configuring Non-GKI Kernels (Legacy Kernels)”


## 已落地的改动

```

arch/arm64/configs/kunlun2_defconfig          # 合并 droidspaces 配置片段
arch/arm64/configs/droidspaces-fragment.config # 片段原文（可追溯/可复用）

.github/workflows/build-kernel.yml              # GitHub Actions 构建
KERNEL-INTEGRATION.md                           # 本文件
```

## 上游内核补丁的适用性判定

Droidspaces v6.6.0 为 non-GKI 内核提供两个补丁（`Documentation/resources/kernel-patches/non-GKI/`）。
**两者在本树均不适用**，判定依据如下（不是“漏打”，是“按树形态核对后不需要”）：

| 补丁 | 目标文件 | 本树实际情况 | 结论 |
|---|---|---|---|
| `01.fix_kernel_panic_in_xt_qtaguid.patch` | `net/netfilter/xt_qtaguid.c` | 文件不存在：`find` 全树无命中、`git ls-files` 无记录、`net/netfilter/Makefile` 无 obj 规则、`net/netfilter/Kconfig` 无该符号；`kunlun2_defconfig` 里残留的 `CONFIG_NETFILTER_XT_MATCH_QTAGUID=y` 是失效行（生成的 `.config` 中根本不出现该符号） | 不适用：qtaguid 已在 Lenovo 这版 Android 13 内核中移除，panic 无从发生 |
| `02.fix_restore cgroup file prefix handling .patch` | `kernel/cgroup/cgroup.c`（cgroup v2 核心） | 本树为 cgroup v1 单核：文件是 `kernel/cgroup.c`，无 `kernel/cgroup/` 目录；`cgroup_file_name()`（`kernel/cgroup.c:1312`）原生就带 `cft->ss && !(cft->flags & CFTYPE_NO_PREFIX) && !(cgrp->root->flags & CGRP_ROOT_NOPREFIX)` 判断，前缀文件名本来就生成 | 不适用：补丁要“恢复”的行为在本树从未被移除 |

判定日期：2026-11-10（thirteen 基线）；2026-11-11 在 lineage-21 与 twelve 两条基线上逐项复核，结论不变。取证方式：`find` / `git ls-files` / `grep` 于本地树，`make ARCH=arm64 kunlun2_defconfig` 生成真实 `.config` 交叉核对。

## 配置对照（以真实 `.config` 为准）

`make ARCH=arm64 O=<dir> kunlun2_defconfig` 后逐项核对，全部 `=y`：


- IPC：`SYSVIPC`、`POSIX_MQUEUE`（两者开启后 `IPC_NS` 自动为 y，符号依赖 `depends on (SYSVIPC || POSIX_MQUEUE)`）
- 命名空间：`NAMESPACES`、`UTS_NS`、`PID_NS`、`USER_NS`、`NET_NS`
- seccomp：`SECCOMP`、`SECCOMP_FILTER`
- cgroup：`CGROUPS`、`CGROUP_DEVICE`、`CGROUP_PIDS`、`MEMCG`、`CGROUP_SCHED`、`FAIR_GROUP_SCHED`、`CGROUP_FREEZER`、`CGROUP_NET_PRIO`
- 文件系统：`DEVTMPFS`、`OVERLAY_FS`、`TMPFS_POSIX_ACL`、`TMPFS_XATTR`
- 网络：`VETH`、`BRIDGE`、`BRIDGE_NETFILTER`、`NF_TABLES`、`IP_NF_TARGET_MASQUERADE`、`NF_NAT_REDIRECT`、`IP_ADVANCED_ROUTER`、`IP_MULTIPLE_TABLES`、`IP_SET`、`NETFILTER_XT_SET`
- 容器内上网：`CONFIG_ANDROID_PARANOID_NETWORK` **显式关闭**（`# ... is not set`）
- 本树不存在的符号（已在片段中注明、未强行添加）：`FW_LOADER_COMPRESS`（4.18 才有）、`NETFILTER_XT_TARGET_MASQUERADE`、`NF_CONNTRACK_NETLINK`、`IP_NF_TARGET_ULOG`（4.9 无此符号）


> 说明：`CONFIG_ANDROID_PARANOID_NETWORK=n` 是 Droidspaces 官方文档对老内核的要求（否则容器内无网络）。代价是把 Android 基于 AID 组的 socket 创建限制放开，属于功能换安全的取舍，刷机前请知悉。

## CI 构建

`.github/workflows/build-kernel.yml`：`ubuntu-22.04` runner + `ubuntu:20.04` 容器 + `gcc-aarch64-linux-gnu`（GCC 9.4 交叉）。

选 GCC 9 的理由：本树多个 vendor 驱动自带 `-Werror`（已实测触发失败的有 `drivers/staging/qcacld-3.0/Kbuild:1661`、`techpack/audio/asoc/codecs/tfa98xx/Kbuild:82`），GCC 11 新增的 `-Warray-parameter`、加强的 `-Wmaybe-uninitialized` 会被这些 `-Werror` 提升为错误。而 `KCFLAGS` 在 kbuild 里排在模块 `ccflags` 之前（`scripts/Makefile.lib:104` 的 `orig_c_flags`），无法用 `-Wno-error` 反覆盖，因此退回与内核年代匹配的 GCC 9，而不是去改 vendor 源码。

- 触发：推送 `droidspaces` 分支 / 手动 `workflow_dispatch`
- 流程：`kunlun2_defconfig` → **断言必需配置项全为 `=y`（缺一即失败）** → `Image.gz` → `Image.gz-dtb`（best effort，设备树在独立仓，可能不含 kunlun2 dtb）→ 上传产物
- 传入 `KCFLAGS="-fno-PIE"`，规避 Debian/Ubuntu 交叉工具链默认 PIE 导致的链接问题


## boot.img 打包（可刷产物）

内核树只产出 `Image` / `Image.gz` / `Image.gz-dtb`，`boot.img` 是 Android 的容器格式，需要 ramdisk、cmdline、地址参数、dtb 与签名。本仓库自带一个不依赖外部工具的封装器把它补齐：

- `bootimg/bootimg.py`：解析/解包/重打包 boot header v0/v1/v2
- `bootimg/stock-payload.bin`（1,667,072 B）：原厂 `boot.img` 从 ramdisk 起始到有效数据末尾的**逐字节**拷贝，含 ramdisk、second、未文档化的对齐区与 dtb 容器
- `bootimg/bootmeta.json`：原镜像 header 的全部字段（page_size 4096、header v2、kernel_addr 0x1000000、ramdisk_addr 0x0、second_addr 0xf00000、tags_addr 0x100、dtb_addr 0x1f00000、os_version 335544642、cmdline 原文）以及 payload 的 sha256

打包只替换内核：`python3 bootimg/bootimg.py repack-from-payload dist/boot.img out/arch/arm64/boot/Image.gz --payload bootimg/stock-payload.bin --meta bootimg/bootmeta.json --pad 67108864`。

自检依据：用原厂内核走同一条路径重打包，第一页之后与原镜像**逐字节一致**（仅 header 里的 `id` 字段不同，该字段是 sha1 摘要，bootloader 不校验）。原厂镜像 sha256 = `a97366fb3cca0d3f3e0e00a561ce04ac72517c596cf26b3506ccd8d7b971f4eb`。

注意：原厂内核是 `4.9.186-perf+`（构建标签 `KUNLUN2_Q_ST_SECURE`，Android Q 时代），本树是 `4.9.317`；原厂 ramdisk 为 32 KB 空 cpio，`second` 399,336 B，dtb 容器 1,232,336 B。

## 已知风险 / 未验证项

- **未在本机编译**（按约定由 GitHub Actions 编译），CI 结果是第一手编译证据。首轮用 GCC 11 失败于 vendor 驱动的 `-Werror`，现改用 GCC 9.4（见上）；若再报错，仍优先怀疑工具链/老代码，而非补丁。
- `Image.gz-dtb` 依赖设备树，而本树的 `arch/arm64/boot/dts/qcom/Makefile` 中**没有** kunlun2 的 `dtb-$(CONFIG_ARCH_SDM670)` 条目（设备树在独立仓库），因此该产物可能不含本机 dtb；`Image.gz` 是主产物。
- 启用 `MEMCG` / `USER_NS` / `DEVTMPFS` 改变了 `task_struct` 等核心结构布局，**必须**与同一份内核一起重编所有 vendor 模块；直接套用旧预编译模块会导致加载失败或开机异常。
- Droidspaces 的 `droidspaces check` 与 SukiSU 管理器的真机验证尚未进行。
