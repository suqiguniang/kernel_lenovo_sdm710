# 内核集成说明 — `droidspaces-eleven` 分支

Lenovo Z6 Youth（L38111 / kunlun2）PixelExperience（Android 11）内核，加入 Droidspaces v6.6.0 支持并产出可刷 `boot.img`。

| 项 | 值 |
|---|---|
| 设备/平台 | Lenovo Z6 Youth（L38111），Qualcomm sdm710（sdm670），`kunlun2_defconfig` |
| 内核版本 | 4.9.268（`LOCALVERSION="-Fratm"`） |
| 上游仓库/分支 | `PixelExperience-Devices/kernel_lenovo_kunlun2` @ `eleven` |
| 基线提交 | `0fa85d1eab9768c3c0826d968eb87dc15648073d`（`Merge branch 'a11/master' of KudProject/kernel_msm-4.9`） |
| 目标 ROM | PixelExperience Eleven（Android 11） |
| 分支 | `droidspaces-eleven` |
| 构建方式 | GitHub Actions（本机不编译） |

## 组件版本（按 commit 锚定，非 tag 猜测）

**Droidspaces v6.6.0**
- 来源：`ravindu644/Droidspaces-OSS`
- commit：`cff50fa04d50472b607ba6822e8bf482b19cd427`（tag `v6.6.0`）
- 依据：`Documentation/Kernel-Configuration.md` → *Configuring Non-GKI Kernels (Legacy Kernels)*

对内核侧只要求**配置项**与（可选的）两个补丁，不需要引入任何上游代码文件。因此本分支的内核侧改动只有 defconfig 与一行构建规则（`kernel/Makefile` 的 IKCONFIG 依赖，见下文），其余是打包工具与 CI。

## 已落地的改动

```
kernel/Makefile                                  # IKCONFIG 改回内嵌真实 .config（见「IKCONFIG」节）
arch/arm64/configs/droidspaces-fragment.config   # 片段原文（唯一事实来源）
arch/arm64/configs/kunlun2_defconfig             # 按片段确定性合并后的结果
tools/merge-config-fragment.py                   # 幂等合并器（--check 供 CI 断言）
tools/required-config.txt                        # CI 断言清单（含必须关闭项）
bootimg/bootimg.py                               # boot header v0 解析/打包/自检
bootimg/header-page.bin                          # 原厂镜像第 1 页，逐字节（4096 B）
bootimg/appended-dtb.bin                         # 原厂内核尾部 8 个 DTB，逐字节（1,221,563 B）
bootimg/stock-kernel.bin                         # 原厂内核载荷 Image.gz-dtb（12,588,115 B），打包器回归基准
bootimg/bootmeta.json                            # 原厂 header 全字段 + 各段 sha256
.github/workflows/build-boot.yml                 # 编译内核 → 打包 boot.img → 产物/Release
KERNEL-INTEGRATION.md                            # 本文件
```

## 上游内核补丁的适用性判定

Droidspaces v6.6.0 为 non-GKI 内核提供两个补丁（`Documentation/resources/kernel-patches/non-GKI/`）。**两者在本树均不适用**，判定依据（2026-11-11，在本分支工作树上取证）：

| 补丁 | 目标文件 | 本树实际情况 | 结论 |
|---|---|---|---|
| `01.fix_kernel_panic_in_xt_qtaguid.patch` | `net/netfilter/xt_qtaguid.c` | 文件不存在：`git ls-files` 无记录、`net/netfilter/` 下无该源文件（本基底未带 qtaguid） | 不适用：panic 无从发生 |
| `02.fix_restore cgroup file prefix handling .patch` | `kernel/cgroup/cgroup.c` | 本树为 cgroup v1 单文件实现：存在 `kernel/cgroup.c`，**无** `kernel/cgroup/` 目录，补丁的 `cgroup_add_file()` 上下文不存在 | 不适用 |

## 配置对照（以真实 `.config` 为准）

`make ARCH=arm64 O=out kunlun2_defconfig` 后逐项核对（`.config` 共 5,535 行），`tools/required-config.txt` 中 77 项全部达标（76 项必须 `=y`，1 项必须关闭）：

- IPC/命名空间：`SYSVIPC`、`POSIX_MQUEUE` → `IPC_NS` 自动为 y；`PID_NS`、`UTS_NS`、`USER_NS`、`NAMESPACES`（`USER_NS` 用于规避 runc/docker 的 unsafe procfs 报错）
- seccomp：`SECCOMP`、`SECCOMP_FILTER`
- cgroup：`CGROUPS`、`CGROUP_DEVICE`、`CGROUP_PIDS`、`MEMCG`、`CGROUP_SCHED`、`FAIR_GROUP_SCHED`、`CGROUP_FREEZER`、`CGROUP_NET_PRIO`
- 文件系统：`DEVTMPFS`、`OVERLAY_FS`、`TMPFS_POSIX_ACL`、`TMPFS_XATTR`
- 网络：`NET_NS`、`VETH`、`BRIDGE`、`BRIDGE_NETFILTER`、`NF_TABLES`（4.9 另需 family/表达式符号，见下）、`IP_NF_TARGET_MASQUERADE`、`NF_NAT_REDIRECT`、`IP_ADVANCED_ROUTER`、`IP_MULTIPLE_TABLES`、`IP_SET`、`NETFILTER_XT_SET`
- 容器内上网：`CONFIG_ANDROID_PARANOID_NETWORK` **显式关闭**（CI 断言必须为 `is not set`）

**本树不存在的符号**（已核 17,304 个 Kconfig 定义，未强行添加）：

| 符号 | 原因 |
|---|---|
| `FW_LOADER_COMPRESS` | 4.18 才引入 |
| `NETFILTER_XT_TARGET_MASQUERADE` | 4.9 用 `IP_NF_TARGET_MASQUERADE` 表达同一功能 |
| `NETFILTER_XT_TARGET_REJECT` | 4.9 用 `IP_NF_TARGET_REJECT` |
| `NF_CONNTRACK_NETLINK` | 本基底无此符号 |
| `IP_NF_TARGET_ULOG` | 本基底无此符号（上游 4.15 已移除） |

**额外开启（上游只列 `NF_TABLES`）**：4.9 里 `NF_TABLES` 只是核心，family 与表达式各自独立成符号；只开核心会得到一个「无法创建任何表」的空壳。片段补了 `NF_TABLES_{INET,IPV4,IPV6,ARP,BRIDGE,NETDEV}` 与 `NFT_{COMPAT,CT,NAT,MASQ,REDIR,REJECT,SET_HASH}` 等常用表达式，容器内 `nft` 才有意义。老内核跑 Docker/Podman 仍建议按上游文档改用 `iptables-legacy` + `cgroupfs`/`vfs`。

## boot.img 结构分析（原厂件实测，非模板猜测）

原厂 `boot.img`（提交本次改造时用户提供，sha256 `ed1d745e…f2a99`）实测：

| 项 | 值 |
|---|---|
| 文件大小 / 分区大小 | 67,108,864 B（64 MiB），尾部 64 B 为 AVB footer |
| header | v0，`page_size=4096`，`kernel_addr=0x8000`、`ramdisk_addr=0x1000000`、`second_addr=0xf00000`、`tags_addr=0x100` |
| `ramdisk_size` / `second_size` / `dt_size` | 0 / 0 / 1（原厂值，原样保留） |
| cmdline | `console=ttyMSM0,115200n8 earlycon=msm_geni_serial,0xA90000 androidboot.hardware=qcom …`（原样保留） |
| 内核载荷 | 12,588,115 B = `Image.gz`（11,366,552 B）+ **8 个追加 DTB**（1,221,563 B），即 `Image.gz-dtb` |
| AVB | footer 指向偏移 `0xC03000` 的 512 B vbmeta；该 vbmeta `authentication_data_block_size=0`、`signature_size=0`、`public_key_size=0`、release `avbtool 1.1.0` → **未签名**，签名链不存在 |

由此得出两条打包策略：

1. **DTB 不重编，逐字节沿用原厂**。本树 `arch/arm64/boot/dts/qcom/Makefile` 只有 `sdm710-qrd-sku2-overlay.dtbo` 这类 overlay 条目，没有任何 `lenovo-kunlun2.dtb` 的 `dtb-$(CONFIG_ARCH_SDM670)` 规则；kunlun2 的设备树在独立仓库。重编出来的 dtb 与 ROM 的 dtbo 组合未必匹配，沿用原厂 8 个 DTB 是唯一可追溯的做法。
2. **不携带 AVB**。原厂 vbmeta 本身未签名（见上），设备必须解锁才能刷机；重打包后镜像变长，vbmeta 的 `image_size=0xC03000` 摘要必然失效，且无法用厂商密钥重签。因此输出镜像把分区尾部（AVB footer 所在 64 B）以及内核载荷之后的空间全部置零，整分区写满 64 MiB——刷入后该分区就是「无 AVB footer」状态，由解锁的 bootloader 直接引导，行为确定（而不是留一个指向新内核字节的失效 footer）。原 vbmeta 所在的 `0xC03000` 已被新内核载荷覆盖，这也是「不保留 AVB」的直接后果：该偏移在内核变大后不再具有任何结构意义。

## 打包器自检（可重复执行）

```
python3 bootimg/bootimg.py selftest --assets bootimg
```

用原厂内核载荷走同一条打包路径重建镜像，再把 header 的 `id` 字段（偏移 576，20 B SHA1）置零后比对：

- 原厂镜像真实长度 `0xC03000` = 12,595,200 B，重建结果长度一致
- `id` 置零后 sha256 = `48cb79b8e12f10c037be72637b841afcf6e5ac94eedd0bb90d9cdb2b42eaf15c`，与原厂件一致 → **第一页之外零差异**，第一页内除 `id` 外零差异
- 唯一差异是 `id`：该字段是构建期 SHA1 摘要，bootloader 不校验；本工具按 AOSP mkbootimg 规则（`sha1(kernel+len, ramdisk+len, second+len)`）重算，故与原厂值不同（原厂 `856aa552…6ddf` 的输入组合试过 7 种都无法复现，说明原厂是别的 mkbootimg 分支/参数算的）

CI 每次构建都先跑这条自检：打包器一旦不再无损，构建在编译前就失败。

## CI 构建

`.github/workflows/build-boot.yml`：`ubuntu-22.04` runner + `ubuntu:20.04` 容器 + `gcc-aarch64-linux-gnu`（GCC 9.4）。

选 GCC 9 的理由：4.9 vendor 驱动自带 `-Werror`，GCC 11 新增的 `-Warray-parameter`、加强的 `-Wmaybe-uninitialized` 会被提升为错误，而 `KCFLAGS` 在 kbuild 里无法反覆盖模块/子目录的 `ccflags`。

流程（任一断言不过即失败，不留半成品）：

1. 打包器原厂 round-trip 自检
2. `kunlun2_defconfig` → `merge-config-fragment.py --check`（defconfig 与片段必须同步）→ 77 项配置断言（76 项 `=y`、1 项必须为 `is not set`）
3. `Image.gz`（`-j$(nproc)`，`KCFLAGS="-fno-PIE"`）
4. 打包 `boot.img`：`header-page.bin` + 新 `Image.gz` + `appended-dtb.bin`，补零到 67,108,864 B
5. `bootimg.py verify`：header 参数与原厂逐项相等、追加 DTB sha256 不变、文件大小等于分区大小
6. 产物：`boot.img`、`Image.gz`、`Image`、`kunlun2-defconfig.resolved`、`BUILD-INFO.txt`（含 sha256）
7. 上传 artifact（保留 30 天）+ 发 Release（tag `boot-eleven-<run_number>`，附 `boot.img`）

本树配置为**全内建**（`.config` 中 `=m` 计数为 0，qcacld Wi-Fi 亦为 `CONFIG_QCA_CLD_WLAN=y`），不产出任何 `.ko`，因此不存在「重编内核后旧 vendor 模块 CRC 对不上」的问题——这是 `MEMCG`/`USER_NS` 这类会挪动 `task_struct`/`cred` 布局的选项能安全开启的前提。

## 刷机

```sh
fastboot flash boot boot.img          # 需要已解锁 bootloader
fastboot reboot
```

刷前建议 `fastboot getvar current-slot`（本机为 A-only，无 slot 后缀）与 `adb shell su -c 'dd if=/dev/block/bootdevice/by-name/boot of=/sdcard/boot-backup.img'` 备份原分区。

刷入后在 Droidspaces App 里跑 *Settings → Requirements → Check Requirements*，或终端 `su -c droidspaces check`。

## IKCONFIG：本树内嵌的是 `arch/arm64/configs/defconfig`，不是构建配置

首次构建后从产出的 `Image` 里抽 IKCONFIG 块核对，抽到的是 `CONFIG_LOCALVERSION="-perf"`、`MEMCG` 未开、5,501 行的配置——与本次构建的 `.config`（`-Fratm`、`MEMCG=y`、5,535 行）完全不符。逐层查到根因：

- 本树 `kernel/Makefile` 被 vendor 改成了 `$(obj)/config_data.gz: arch/arm64/configs/defconfig FORCE`，而主线 4.9 是 `$(obj)/config_data.gz: $(KCONFIG_CONFIG) FORCE`
- 因此 `CONFIG_IKCONFIG` 内嵌的 `/proc/config.gz` 是**仓库里那份参考 defconfig**（5,501 行，`sha256 b529d56d…1d80c`），与这次实际编译用的 `.config` 无关
- 实证：从 `Image` 抽出的 146,046 B 配置块解压后与 `arch/arm64/configs/defconfig` **逐字节相同**（`diff` 0 行）

这不是「不影响开机」的小事：排查容器功能时 `zcat /proc/config.gz | grep MEMCG` 会给出相反答案，droidspaces 一类工具若读 `/proc/config.gz` 判断能力也会被误导。本分支把该依赖改回 `$(KCONFIG_CONFIG)`，重建后 `/proc/config.gz` 即等于构建真值。改动仅影响内嵌配置块，不碰任何编译产物逻辑。

## 产物核验（配置层 → 镜像层 → 二进制层）

**配置层**（CI，`out/.config`，5,535 行）：`tools/required-config.txt` 77 项断言全过；`ANDROID_PARANOID_NETWORK` 为 `is not set`；`=m` 计数 0（全内建）。

**镜像层**（本机，run 2 的 Release 资产 `boot.img`，下载后 sha256 与 `BUILD-INFO.txt` 一致）：`2e5e1e9e…29200`，67,108,864 B

| 检查 | 结果 |
|---|---|
| header 参数（`kernel_addr`/`ramdisk_addr`/`second_addr`/`tags_addr`/`page_size`/`cmdline`/`name`/header 版本） | 与原厂逐项相等 |
| 追加 DTB | `sha256 7cd9cc55…f8fc41`，与原厂一致（8 个 DTB，1,221,563 B） |
| 载荷 | `Image.gz` 16,182,571 B + DTB 1,221,563 B = 17,404,134 B；gz 的 sha256 与产物 `Image.gz` 一致，解压后 sha256 与产物 `Image` 一致 |
| 尾部 | 载荷之后全零，末 64 B 全零（无 AVB footer） |
| 内核 banner | `Linux version 4.9.268-Fratm (gcc version 9.4.0 (Ubuntu 9.4.0-1ubuntu1~20.04.2))` |

**二进制层**（在 `Image` 里找「只有开了该选项才存在的字符串」，用同机同容器的原厂 4.9.112 镜像做阴性对照）：

| 探针字符串 | 来源 | 本产物 | 原厂 4.9.112 |
|---|---|---|---|
| `shmmax`、`msgmnb` | `CONFIG_SYSVIPC` 的 sysctl | 1 / 1 | 0 |
| `oom_control` | `CONFIG_MEMCG` 的 cgroup 文件 | 2 | 0 |
| `max_user_namespaces` | `CONFIG_USER_NS` 的 sysctl | 1 | — |
| `nf_tables` | `CONFIG_NF_TABLES` | 6 | 0 |
| `ip_set` / `hash:ip` | `CONFIG_IP_SET` | 12 / 1 | 2 / — |

## 关于用户提供的「原版 boot」

提供的 `boot.img`（`sha256 ed1d745e…2a99`）内核 banner 是 `4.9.112-perf+ (buildslave@byws28) (gcc 4.9.x) 2019-06-07`，内嵌配置 `-perf`、5,501 行——与仓库里 `kernel_lenovo_sdm710-pie`（4.9.112、`CONFIG_LOCALVERSION="-perf"`）同一条线，即**联想原厂 Pie 内核**，不是 PE eleven 的内核。

本产物沿用的是它的**容器参数**（header 布局、加载地址、cmdline、8 个 DTB、分区大小），内核换成 PE eleven 树的 4.9.268。也就是说：镜像结构对齐你给的这份 boot，内核来源是 PE eleven 树。若设备当前跑的是 PE eleven 而不是 Pie，PE 自己的 boot.img 才是更合适的模板（cmdline / ramdisk 可能不同），替换方式只需一条命令：

```sh
python3 bootimg/bootimg.py import-stock <PE的boot.img> --out bootimg/
# 然后重跑 CI 即可，header 参数与 DTB 会自动取自新模板
```

## 已知风险 / 未验证项

- **未做真机验证**：CI 绿只代表编译与打包通过，不等于可刷机、能开机。首次刷入前务必留好原 boot 分区镜像。
- **AVB 结构被移除**：见上，依赖解锁状态。若设备处于锁定状态，本镜像不可刷也不可引导。
- **cmdline/地址参数沿用用户提供的原厂 boot**（`buildvariant=user`、`androidboot.configfs=true` 等，见「关于用户提供的『原版 boot』」）。目标 ROM 若不认这套 `androidboot.*`，语义会不一致；换模板只需重跑 `import-stock`。
- **内核版本跨度大**：4.9.112（原厂 Pie）→ 4.9.268（PE eleven）。同 SoC、同 DTB 组，理论上可引导，但 userspace/vendor 侧的 SELinux 策略、内核头与 HAL 期望值都是按各自 ROM 的配套内核调的，跨代组合属未验证区。
- **容器内联网**依赖 `CONFIG_ANDROID_PARANOID_NETWORK=n`，代价是放开 Android 基于 AID 组的 socket 创建限制，属功能换安全的取舍。
- 老内核 + 现代容器工具：nftables 可用但功能面窄于 5.x；Docker/Podman 的 `BPF_CGROUP_DEVICE` 在本代内核不存在，需要 `cgroupfs` 驱动 + `vfs` 存储驱动。
