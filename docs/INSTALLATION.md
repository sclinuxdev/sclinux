# 🧱 ShenChen Linux 安装规范与磁盘布局设计

**Spec Version:** 1.0  
**Status:** Draft  
**Target Firmware:** UEFI x86_64  
**Root Filesystem:** XFS on LVM thin  
**Bootloader:** systemd-boot (推荐) / GRUB (备选)

> [!IMPORTANT]
> **当前可执行范围**：本文第 3–6 章（分区、LVM、文件系统、引导器）使用的全部是标准 Linux 工具，**今天即可完整执行**。
> 第 7 章起的系统 bootstrap 依赖 `sage` 的软件仓库与 `base` 包集，**二者尚未建立**，该部分目前是目标设计而非可跟做步骤。详见 [§9 当前阻塞项](#9-当前阻塞项)。

---

## 目录

1. [设计目标与取舍](#1-设计目标与取舍)
2. [为什么是 XFS on LVM thin](#2-为什么是-xfs-on-lvm-thin)
3. [磁盘布局](#3-磁盘布局)
4. [引导器选型](#4-引导器选型)
5. [LVM thin 配置](#5-lvm-thin-配置)
6. [文件系统创建与挂载](#6-文件系统创建与挂载)
7. [系统 bootstrap](#7-系统-bootstrap)
8. [快照与回滚流程](#8-快照与回滚流程)
9. [当前阻塞项](#9-当前阻塞项)
10. [已知限制](#10-已知限制)

---

## 1. 设计目标与取舍

| 目标 | 手段 | 代价 |
| :--- | :--- | :--- |
| 日常 I/O 无 CoW 元数据撕裂 | 根文件系统为裸 XFS | 文件系统层不提供快照 |
| `sage rebuild` 可回滚 | 块级快照下沉到 LVM thin | 多一层 device-mapper |
| 引导路径无兼容性雷区 | 内核置于 ESP，systemd-boot 直接加载 | ESP 需 1 GiB 而非 512 MiB |

核心立场：**CoW 本身不是问题，问题是让 CoW 污染每一次日常写入。** 把它限制在快照层，既保住裸 XFS 的确定性 I/O，又拿回声明式重构必需的回滚能力。

---

## 2. 为什么是 XFS on LVM thin

### 2.1 XFS 承担日常 I/O

XFS 无 CoW，写入就地进行，不产生随机写场景下的元数据碎片退化。它在并行 I/O、大文件、在线扩容上表现成熟，且已被 RHEL 系发行版作为默认根文件系统长期大规模验证。

### 2.2 LVM thin 承担回滚

XFS 不提供快照，而 `sage rebuild` 会对系统底座做原子置换——没有回退手段时，一次失败的重构就是一次救援盘启动。LVM thin 快照填补这个缺口：

- 快照创建是常数时间，不复制数据
- CoW 只在被快照的卷发生写入时触发，且只影响该卷
- 回滚是卷级操作，与文件系统类型无关

### 2.3 为什么不选其他方案

| 方案 | 否决理由 |
| :--- | :--- |
| **Btrfs** | 技术上可行，但 CoW 作用于每一次写入，正是本项目要规避的退化模式 |
| **ZFS** | 快照能力最强，但 CDDL 导致树外模块，每次内核升级需重新构建，与「纯血」定位冲突 |
| **bcachefs** | 在 mainline 中的维护地位近年反复变动，不适合作为发行版根文件系统的赌注 |
| **纯 XFS 无快照层** | 立场自洽（「不回滚，只重建」），但 `rebuild` 中途损坏内核或 init 时无恢复路径 |

### 2.4 ESP 无法是 XFS

UEFI 规范要求 EFI 系统分区为 FAT 文件系统，这是固件层面的硬性约束，无法绕过。

因此项目口号中的「全盘原生 XFS」**精确表述应为「除 ESP 外全盘原生 XFS」**。ESP 仅承载引导器与内核镜像，不参与任何系统运行时 I/O。

---

## 3. 磁盘布局

```
/dev/sda
├─ sda1   1 GiB    EF00  FAT32   /boot/efi   ESP：引导器 + 内核 + initramfs
└─ sda2   剩余全部  8E00  LVM PV
          └─ vg0
             └─ thinpool  (data + metadata)
                ├─ root   XFS   /
                └─ home   XFS   /home
```

**没有独立 `/boot` 分区**——内核直接置于 ESP，由 systemd-boot 加载。这一决定同时规避了两个兼容性问题，见 §4。

### 3.1 分区命令

```bash
# 清空并建立 GPT
sgdisk --zap-all /dev/sda
sgdisk --clear /dev/sda

# ESP：1 GiB，容纳多个内核代次
sgdisk --new=1:0:+1G     --typecode=1:EF00 --change-name=1:"EFI System" /dev/sda

# LVM 物理卷：剩余全部空间
sgdisk --new=2:0:0       --typecode=2:8E00 --change-name=2:"ShenChen LVM" /dev/sda

sgdisk --print /dev/sda
```

> ESP 定为 1 GiB 而非常见的 512 MiB，是因为内核与 initramfs 都住在这里。按每代次约 150 MiB 估算，1 GiB 可容纳约 5 个内核代次，足够回滚周转。

---

## 4. 引导器选型

这是本设计中影响最大的单个决策，因为它同时受两个约束夹击。

### 4.1 两个约束

**约束一：GRUB 读不了 LVM thin volume。** GRUB 的 `lvm` 模块支持 linear、striped、mirror，**不支持 thin provisioning**。若 `/` 位于 thin LV 且 `/boot` 也在其中，GRUB 无法加载内核。

**约束二：mkfs.xfs 的新特性曾使 GRUB 无法引导。** XFS v5 陆续默认启用 `bigtime`、`inobtcount` 等特性，历史上出现过新建的 XFS 分区无法被当时的 GRUB 读取的情况。

### 4.2 结论：systemd-boot

systemd-boot 是 EFI stub 加载器，**只从 ESP 读取**，完全不接触 XFS 与 LVM。两个约束同时消失。

```bash
bootctl --esp-path=/mnt/boot/efi install
```

代价是内核必须置于 ESP（已在 §3 布局中预留空间），以及 ESP 内容需与内核包同步——由 `sage` 的 triggers 机制负责。

### 4.3 备选：GRUB

若因其他原因必须使用 GRUB（如需要引导菜单编辑、多系统共存复杂场景），布局须改为**独立 `/boot` 普通分区**，绕开 thin LV：

```
sda1   512 MiB   EF00   FAT32   /boot/efi
sda2   1 GiB     8300   XFS     /boot        ← 普通分区，不进 LVM
sda3   剩余      8E00   LVM PV  → thinpool → root / home
```

并且 `/boot` 的 XFS 必须关闭可能引发兼容问题的特性：

```bash
mkfs.xfs -m bigtime=0,inobtcount=0 -L SC_BOOT /dev/sda2
```

> 该参数仅用于 GRUB 路径下的 `/boot`。根文件系统应使用默认特性集，不要为兼容性牺牲根分区的能力。

---

## 5. LVM thin 配置

```bash
# 物理卷与卷组
pvcreate /dev/sda2
vgcreate vg0 /dev/sda2

# thin pool：留出 5% 余量给元数据与未来扩容
lvcreate --type thin-pool \
         --name thinpool \
         --extents 95%FREE \
         --poolmetadatasize 1G \
         --chunksize 256K \
         vg0

# thin volume：virtualsize 可超过池容量，按需分配
lvcreate --thin --name root --virtualsize 60G  vg0/thinpool
lvcreate --thin --name home --virtualsize 200G vg0/thinpool
```

### 5.1 元数据尺寸

thin pool 的元数据区**耗尽后果严重**——池会转入只读，正在进行的写入失败，且恢复过程繁琐。这是本方案最需要防守的失效模式。

近似关系为 `元数据大小 ≈ 池容量 ÷ chunk_size × 64 字节`。上面显式指定 `--poolmetadatasize 1G` 配合 `--chunksize 256K`，对数 TB 级的池留有充裕余量。

### 5.2 必须启用自动扩容

```bash
# /etc/lvm/lvm.conf
activation {
    thin_pool_autoextend_threshold = 80
    thin_pool_autoextend_percent   = 20
}
```

未启用自动扩容时，thin pool 写满即数据损坏风险。**这不是可选优化项。** 同时应部署对 `dmeventd` 告警的监控。

---

## 6. 文件系统创建与挂载

```bash
mkfs.fat -F32 -n SC_ESP /dev/sda1
mkfs.xfs  -L SC_ROOT /dev/vg0/root
mkfs.xfs  -L SC_HOME /dev/vg0/home
```

根与家目录使用 **mkfs.xfs 默认特性集**——它们由内核直接挂载，不经过引导器，没有理由降级特性。

```bash
mount /dev/vg0/root /mnt
mkdir -p /mnt/home /mnt/boot/efi
mount /dev/vg0/home /mnt/home
mount /dev/sda1     /mnt/boot/efi
```

---

## 7. 系统 bootstrap

> [!WARNING]
> 本章依赖尚不存在的软件仓库与 `base` 包集，见 [§9](#9-当前阻塞项)。以下为目标流程。

安装器的核心是 `sage` 已经实现的 `--root` sysroot 隔离能力——整个 bootstrap 本质上是围绕它的一层脚本，而非独立的安装程序。

```bash
# 将底座包集安装进 /mnt
sage --root /mnt install base linux glibc systemd

# 写入声明式系统状态
install -Dm644 /dev/stdin /mnt/etc/sage/system.toml <<'TOML'
schema_version = 1

[system]
root_dir   = "/"
db_path    = "/var/lib/sage/data.mdb"
cache_dir  = "/var/cache/sage"
config_dir = "/etc/sage"

[providers]
init = "systemd"
udev = "systemd-udevd"
libc = "glibc"
TOML

# 生成 fstab（使用 UUID，不依赖设备名顺序）
genfstab -U /mnt >> /mnt/etc/fstab

# 安装引导器
bootctl --esp-path=/mnt/boot/efi install
```

### 7.1 initramfs 要求

根文件系统位于 thin LV 之上，initramfs **必须包含 LVM 用户空间工具与 device-mapper 模块**，否则内核无法激活卷组、找不到根设备。这是本布局相对普通分区方案唯一增加的 bootstrap 要求。

---

## 8. 快照与回滚流程

这是 LVM thin 层价值的兑现处，也是本方案存在的理由。

### 8.1 重构前打点

```bash
lvcreate --snapshot --name root-pre-rebuild vg0/root
sage rebuild
```

thin 快照创建为常数时间操作，不复制数据，因此可以无成本地在每次 `rebuild` 前执行。建议由 `sage` 的 pre-transaction hook 自动完成。

### 8.2 回滚

```bash
lvconvert --merge vg0/root-pre-rebuild
reboot
```

> 合并**不会立即生效**。根卷处于挂载使用中，LVM 会将合并调度到该卷下次激活时执行——即重启后。这是正常行为，不是失败。

### 8.3 清理

```bash
lvremove vg0/root-pre-rebuild
```

确认新状态无误后应及时删除快照。长期保留的快照会持续累积 CoW 数据，消耗 thin pool 空间。

---

## 9. 当前阻塞项

本文第 7 章无法执行，缺口如下：

| 缺口 | 说明 |
| :--- | :--- |
| **软件仓库** | 无任何已发布的 `*.pkg.tar.zst` 与 `index.toml`。`sage repo index` 可生成索引，但没有包可索引 |
| **`base` 包集** | 「底座包集包含哪些包」尚未定义 |
| **内核包** | 无 `linux` 包，也无内核构建配置 |
| **triggers 联动** | ESP 内的内核与 initramfs 需随包更新同步，依赖 `triggers.toml` 机制落地 |
| **`genfstab`** | 该工具来自 arch-install-scripts，需自备等价实现或引入 |

在此之前，本文第 3–6 章可用于准备一块符合规范的磁盘，第 7 章之后需手工完成或等待上述组件。

---

## 10. 已知限制

- **XFS 不可缩小。** 只能在线扩容，无法收缩。分区规划失误只能重建，这是选择 XFS 必须接受的代价。
- **快照是块级而非文件级。** 回滚是整卷回退，无法只恢复单个文件。需要文件粒度恢复能力时应另行部署备份方案——快照不是备份。
- **thin pool 写满风险。** 见 §5.2，自动扩容与监控是强制要求而非建议。
- **多一层 device-mapper。** `lsblk` 与故障排查的复杂度高于普通分区方案。救援场景下需要手工 `vgchange -ay` 激活卷组。
- **ESP 需要维护。** 内核置于 FAT32 分区意味着它不受 XFS 的日志保护，且需要与包管理器状态保持同步。
