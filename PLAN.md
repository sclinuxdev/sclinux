# ShenChen Linux x86_64 / aarch64 双架构可复现构建计划

**状态：** 执行中

**基线：** `main` @ `abce051`

**工作分支：** `build/reproducible-multiarch`

**目标产物：** 可在 UEFI 虚拟机中启动的 x86_64 与 aarch64 qcow2 镜像

## 1. 目标与基准

本计划把当前只有配方和设计文档的仓库，补全为可以从固定输入构建 Stage1、组装系统镜像并自动验证启动的双架构工程。

现有 x86_64 qcow2 **只作为底层启动事实的参考样本**，用于确认 glibc、GNU 基本命令、systemd、XFS 等底层方向。它不是完整功能的黄金镜像：SHC 别名、Sage 行为和其他上层功能必须以仓库 policy、固定源码和自动化测试为准。

“x86_64 与 aarch64 是同一个系统”定义为：

- 使用同一份发行版 policy、Stage1 清单、配方版本和源码锁；
- 使用相同的 `/usr` merge、glibc、GNU coreutils、systemd、XFS on LVM thin 与 systemd-boot 设计；
- 架构无关的配置、systemd unit、SHC/Sage 命令语义和验收测试相同；
- 架构差异必须落在第 2 节白名单内，并能从构建元数据中解释；
- 不以旧镜像的文件逐字节相同、包排序偶然差异或未实现功能作为一致性标准。

仓库中的直接依据：

- `docs/DISTRO_POLICY.md:27-39` 定义了 glibc、GNU coreutils、systemd、XFS on LVM thin、systemd-boot 与 `/usr` merge；
- `docs/DISTRO_POLICY.md:138-148` 要求每个上游源码 URL 都有 SHA256；
- `docs/INSTALLATION.md:73-85` 定义 GPT、ESP、LVM thin、XFS 的磁盘布局；
- `docs/INSTALLATION.md:117-127` 选择 systemd-boot，并指出内核/ESP 同步仍缺自动机制；
- `Stage1/manifest.toml:1-5` 声明 Stage1 应在 Stage0 chroot 中全量重建，但仓库当前没有所引用的 `stage1/scripts/gen-order.py` 或 Stage0 实现；
- `tests/checksum-debt.txt` 记录了仍为空的源码校验和；
- `Stage1/recipes/sage/recipe.toml:37`、`Stage1/recipes/gmp/recipe.toml:27` 和 `Stage1/recipes/linux-zen/recipe.toml:40-41` 仍含 x86_64 或本地 distfiles 硬编码。

## 2. 架构差异白名单

以下差异不影响“同一系统”的判定：

| 类别 | x86_64 | aarch64 |
| --- | --- | --- |
| GNU target triplet | `x86_64-pc-linux-gnu` | `aarch64-unknown-linux-gnu` |
| ELF machine / ABI | x86-64 | AArch64 |
| glibc 动态加载器 | `ld-linux-x86-64.so.2` | `ld-linux-aarch64.so.1` |
| Linux 内核镜像 | `arch/x86/boot/bzImage` | `arch/arm64/boot/Image` |
| UEFI 默认入口 | `BOOTX64.EFI` | `BOOTAA64.EFI` |
| 内核配置 | Q35/常用 x86_64 设备 | QEMU virt/常用 ARM64 设备 |
| 包架构字段 | `x86_64` | `aarch64` |

除此之外出现的包版本、源码哈希、关键配置、服务启用状态或命令行为差异，都视为需要修复或记录的新决策。

## 3. 本阶段范围

本阶段覆盖可启动、可登录、可验证的基础系统：工具链、glibc、基本命令、systemd/udev、网络基础、内核、initramfs、LVM2、XFS、UEFI 引导、Sage 和 SHC。

Wayland、桌面合成器、图形登录器、安装器 UI 与实体硬件全面兼容不作为本阶段的完成条件；它们应建立在双架构基础镜像通过之后。

## 4. 实施阶段

### 阶段 A：冻结构建契约与输入

产物：

- 本文档及持续更新的执行状态；
- 架构名称、target triplet、内核路径和 UEFI 路径的集中配置；
- 固定 Sage 上游提交；
- 所有 `[source]` 的非空 SHA256；
- Linux Zen 补丁作为显式、可校验输入，补丁失败必须终止构建。

通过条件：

- `tests/checksum-debt.txt` 清空并移除临时豁免；
- 配方校验器拒绝空 SHA256、未知架构和不存在的本地补丁；
- 源码预取在下载内容不匹配时返回非零状态。

### 阶段 B：补齐 Stage0 与统一构建入口

建立最小的 architecture-matched Linux 构建环境，用它提供 Stage0 工具；Stage1 中列出的包仍从固定源码完整重建，宿主系统二进制不得复制进最终 rootfs。

计划新增：

- `Stage0/`：bootstrap 输入、版本锁与说明；
- `tools/build.py`：`fetch`、`stage0`、`stage1`、`rootfs`、`image`、`test` 子命令；
- `out/<arch>/`：未跟踪的下载、包、rootfs、镜像和日志目录。

通过条件：

- `tools/build.py --arch x86_64 stage1` 与 `--arch aarch64 stage1` 走同一控制流；
- 从空的 `out/<arch>` 开始可以重放；
- 构建日志记录源码哈希、配方哈希、宿主信息与每个包结果；
- Stage1 依赖拓扑可自动生成，且与 `Stage1/manifest.toml` 一致。

### 阶段 C：配方双架构参数化

工作内容：

- 消除 Sage、GMP、glibc、Linux 等配方中的 x86_64 硬编码；
- 为 recipe schema 增加 `x86_64`、`aarch64`、`any` 校验；
- 只在确有差异时使用架构条件，不复制两套完整配方；
- 保证拆分包在两个架构上的依赖关系相同。

通过条件：

- 静态检查中不再出现未列入白名单的 `x86_64`/`aarch64` 字面量；
- manifest 中每个包的直接依赖都存在，拓扑顺序无逆序；
- 两个架构生成相同的逻辑包名与版本集合，`arch = "any"` 包内容哈希相同。

### 阶段 D：补齐可启动系统包集

至少补齐并接入：

- `lvm2`、`xfsprogs`、`dosfstools`、`gptfdisk`、`efibootmgr`；
- systemd-boot/EFI 文件安装；
- mkinitcpio 的 LVM2、XFS、udev 钩子；
- x86_64 与 aarch64 的 Linux 配置；
- 内核、initramfs 与 ESP 更新触发流程。

通过条件：

- initramfs 内包含 device-mapper/LVM、XFS 和对应架构所需的存储驱动；
- 内核更新后能够自动刷新 initramfs 与 ESP 内容；
- `base` 元包能完整拉入一次可启动安装所需的软件，不依赖手工补包。

### 阶段 E：rootfs 与 qcow2 组装

镜像布局遵循 `docs/INSTALLATION.md`：GPT + 1 GiB FAT32 ESP + LVM PV + thin pool + XFS root/home。设备名、分区 GUID、文件系统 UUID 与构建时间的处理规则必须写入元数据。

每次构建输出：

- `shenchen-<arch>.qcow2`；
- `packages.lock`：包名、版本、架构与包哈希；
- `sources.lock`：URL、版本与源码 SHA256；
- `build-info.json`：Git 提交、构建参数、工具版本和允许的架构差异；
- 完整构建日志。

通过条件：

- `qemu-img check` 返回成功；
- GPT、ESP、LVM thin 与 XFS 标签符合规范；
- rootfs 中 `/bin`、`/sbin`、`/lib`、`/lib64` 指向 `/usr` 对应目录；
- 镜像中不存在来自宿主机的绝对路径或未声明二进制。

### 阶段 F：x86_64 验证

先用新流程重建 x86_64。旧 qcow2 只用于比较底层事实，不要求复刻其未完成的上层状态。

QEMU 目标：`q35`、UEFI、virtio 磁盘，并额外覆盖 UTM 当前使用的 IDE/e1000 兼容启动。

通过条件：

- 通过 UEFI 启动到 systemd `multi-user.target`；
- 根文件系统实际为 XFS，底层为 LVM thin；
- 无 emergency mode、failed unit 或 kernel panic；
- glibc、coreutils、bash、systemd、网络基础命令可运行；
- SHC/Sage 的架构无关行为测试全部通过；
- 生成串口日志和测试结果文件。

### 阶段 G：aarch64 构建与验证

使用相同源码锁、manifest、镜像布局和验收脚本构建 aarch64；QEMU 目标为 `virt` + AArch64 UEFI + virtio。

通过条件：

- aarch64 镜像通过 UEFI 启动到同一 systemd target；
- 阶段 F 的全部架构无关测试通过；
- `uname -m`、ELF machine、动态加载器、内核/EFI 文件只呈现第 2 节允许的差异；
- 双架构 `packages.lock` 的包名/版本集合一致，例外必须显式列出原因。

### 阶段 H：文档、CI 与交付

工作内容：

- 更新 `README.md`、`docs/DISTRO_POLICY.md`、`docs/INSTALLATION.md`，消除“仅 x86_64”及已经过时的阻塞描述；
- CI 保留快速 recipe/SHC/文档检查，并加入架构矩阵的构建前检查；
- 完整 Stage1 与启动测试保留可重放的日志和产物；
- 更新中文 `CHANGELOG.md`。

通过条件：

- 现有测试继续通过；
- 新增 checksum、架构、拓扑、镜像布局与启动测试通过；
- 文档中的命令能从干净工作目录执行；
- 提交前工作树只包含本计划范围内的文件。

## 5. 总体验收标准

项目完成本计划需要同时满足：

1. 一条统一入口命令分别生成 x86_64 与 aarch64 qcow2；
2. 两个镜像均在 QEMU UEFI 环境自动启动到 systemd `multi-user.target`；
3. 两个镜像均使用 XFS on LVM thin，ESP 为 FAT32；
4. 所有外部源码和本地补丁都固定版本并校验 SHA256；
5. 两个架构拥有相同的逻辑包名/版本集合，差异仅来自第 2 节白名单；
6. 同一套架构无关测试验证基本命令、systemd、SHC 与 Sage；
7. 构建产物附带足以复查来源和差异的锁文件、元数据及串口日志；
8. 现有 CI 检查保持通过，新增测试没有依赖旧 qcow2 的未完成行为。

## 6. 风险与处理

| 风险 | 处理方式 |
| --- | --- |
| 现有配方可以通过静态校验但实际无法编译 | 先做源码预取与小型闭环，再逐批推进 Stage1；每个失败记录到具体包，不绕过错误 |
| 旧 x86_64 镜像与仓库目标不一致 | 旧镜像只提供底层事实；上层以 policy、源码锁和测试为准 |
| aarch64 配方被隐式 x86 假设污染 | 集中架构配置、硬编码扫描和真实 aarch64 构建三重验证 |
| Linux Zen 补丁缺失或静默失败 | 补丁变为显式带哈希输入，删除 `|| true`，失败立即停止 |
| LVM thin 根卷在 initramfs 阶段不可见 | initramfs 内容检查加真实启动测试，不以“成功生成文件”代替可启动验证 |
| qcow2 时间戳/UUID 影响逐字节复现 | 包与 rootfs 内容先做确定性验证；镜像随机字段集中记录并逐步固定，不用一个总哈希掩盖原因 |
| 完整 Stage1 构建时间过长 | 快速静态测试、小型 bootstrap smoke 和完整构建分层运行，缓存只加速、不作为唯一输入 |

## 7. 执行状态

| 阶段 | 状态 |
| --- | --- |
| 基线同步与仓库审计 | 已完成 |
| A：构建契约与输入 | 进行中 |
| B：Stage0 与统一入口 | 未开始 |
| C：配方双架构参数化 | 未开始 |
| D：可启动系统包集 | 未开始 |
| E：rootfs/qcow2 组装 | 未开始 |
| F：x86_64 验证 | 未开始 |
| G：aarch64 验证 | 未开始 |
| H：文档、CI 与交付 | 未开始 |

每完成一个阶段，应在本表更新状态，并在 `CHANGELOG.md` 记录对应提交和可复查的测试结果。
