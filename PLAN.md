# ShenChen Linux x86_64 / aarch64 双架构可复现构建计划

**状态：** 执行中

**基线：** `main` @ `c49d13b`（已于功能分支合并）

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
- `tests/checksum-debt.txt` 当前没有债务条目，全树有源码 URL 的配方都固定 SHA256；
- `config/architectures.toml` 集中声明两种架构差异，Stage1 canonical recipes 保持架构无关。

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

- `tests/checksum-debt.txt` 没有债务条目或临时豁免；
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
| A：构建契约与输入 | 已完成 |
| B：Stage0 与统一入口 | 已完成 |
| C：配方双架构参数化 | 已完成 |
| D：可启动系统包集 | 已完成（双架构 120/120；Sage 与 SHC 已进入 base 闭包） |
| E：rootfs/qcow2 组装 | 已完成（双架构 rootfs 与 16 GiB UEFI qcow2 均已生成并通过 `qemu-img check`） |
| F：x86_64 验证 | 已完成（干净基镜像通过 qemu-img，临时 overlay 经 OVMF/TCG 启动；120 包与 49,028 个登记文件全部匹配，failed unit 为零） |
| G：aarch64 验证 | 已完成（Docker 卷恢复后重新校验；干净基镜像经 AAVMF/TCG 启动，120 包与 43,648 个登记文件全部匹配，failed unit 为零） |
| H：文档、CI 与交付 | 已完成（静态门禁、双架构运行门禁、最终哈希与问题记录均已固化；功能分支单独交付，未合并 `main`） |

每完成一个阶段，应在本表更新状态，并保留对应提交和可复查的测试结果。

### 7.1 最终产物证据

| 架构 | rootfs SHA-256 | qcow2 SHA-256 | 真实启动门禁 |
| --- | --- | --- | --- |
| x86_64 | `c910ca7a4f2c9c2ed40776cf026ef36c2c33ad083d0d1adf122672a4c6aa2cdf` | `a995a1d3bd4b538419471c105a5c6867e157ddf927553612de9b98a1c0a4e205` | OVMF/TCG；120 包；49,028/49,028 文件匹配；XFS on `vg0/root`；failed unit 为零 |
| aarch64 | `70e35cc97872c5e002aa908367c8bee10b288bb8a8b8f3f7ca1936edef751e36` | `88491e4646e9450d0b598f5e99078b231d3c780eedc522f5f45a00e1a7944a00` | AAVMF/TCG；120 包；43,648/43,648 文件匹配；XFS on `vg0/root`；failed unit 为零 |

两个启动门禁都只写临时 qcow2 overlay；正常关机后删除 overlay，再次计算的基础镜像哈希与启动前一致。

## 8. 实施问题记录

这里记录实施过程中实际遇到的问题、处理状态和上游跟踪；它是计划执行记录，不是 CHANGELOG。

| 问题 | 影响与根因 | 处理与验证 | 状态 / 上游 |
| --- | --- | --- | --- |
| Sage 的 usr-merge、触发器与包身份修复需要跟随 PR #8 后继续收口 | 旧基线仍可能在 upgrade、触发失败或路径别名场景产生状态偏差 | 固定并重建 Sage PR #14 提交，Stage1 GCC 15.3 集成测试 15/15、GitHub Actions 通过；SCLinux 双架构 Sage 包升级到 release 13 | 已修复；[Sage PR #14](https://github.com/sclinuxdev/sage/pull/14) |
| 受限 x86_64 构建机自带 PRoot 5.1.0，目标 glibc 的 `faccessat2` 路径检查不受支持 | 内核文件真实存在且 `stat` 可见，但 mkinitcpio 的 Bash `-r` 判断错误，导致 initramfs trigger 失败 | 固定使用 PRoot 5.4.0（首次加入 `faccessat2`），包装器规范化 rootfs 绝对路径；从空 rootfs 重装 120 包及三项触发器一次成功 | 已修复；构建环境要求已写入安装文档 |
| xmake 的真实二进制安装为 `xmake.real`，Stage1 wrapper 刷新后缺少 `xmake` 入口 | 构建完成的 channel 无法经 Sage profile 调用 xmake | wrapper 目录在存在 `xmake.real` 时创建同目录 `xmake` 链接；fixture 与真实 x86_64 toolchain 均通过 | 已修复；属于本仓库工具隔离问题 |
| QEMU smoke test 在终端回显的命令文本里提前匹配 `SCLINUX_GATE_PASS` | 来宾命令尚未执行就可能被误报为通过，无法证明 Sage、SHC 或 failed-unit 门禁 | 登录后先关闭 TTY echo，再发送 gate；只有来宾实际输出 PASS 才关机，失败则打印 unit 诊断并返回非零 | 已修复；真实门禁已揭示并阻止 oomd 问题 |
| ARM64 在 x86_64 TCG 下的完整校验超过 QEMU smoke 固定的 600 秒等待上限 | 第一轮已进入来宾并执行 `sage verify`，但 expect 先超时，测试框架错误中止 QEMU | 支持正整数 `SCLINUX_QEMU_TIMEOUT`，默认仍为 600 秒；以 1800 秒从干净 overlay 重跑，43,648/43,648 文件匹配并正常关机 | 已修复并记录构建环境用法 |
| systemd-oomd 默认启用但 base-files 缺少 `systemd-oom` 用户和组 | QEMU 启动到 multi-user 后 unit 以 `217/USER` 反复失败 | base-files release 6 固定 UID/GID 994；双架构最终 overlay 启动后 Sage verify 全通过，failed unit 均为零 | 已修复并完成双架构复验 |
| macOS 数据卷写满导致 Docker 在 ARM rootfs 导出时退出 | 首次远端流式导出只得到 559 MiB `.part`；第二次本地归档触发 Docker VM ext4 `potential data loss`，不得沿用此前 ARM 卷校验结论 | 两次失败归档均与成品隔离并删除本地残片；恢复 Docker 后从卷内重跑包数、43,648 文件 Sage verify、ELF 架构与引导门禁；分块上传后的远端归档通过 zstd 与 SHA-256 校验 | 已恢复并完成独立复验 |
| systemd 首次启动时 IMDS generator 早于 hardware database 重建 | 串口日志出现一次缺少 hwdb 的 generator 警告，但 hardware database 随后成功生成 | 启动门禁检查 `systemctl --failed` 为空，且登录、包校验与正常关机均通过 | 已记录为首次启动日志噪声，不影响交付门禁 |
| libguestfs appliance 未包含主机后装的 `thin_check` | 组装时 LVM 跳过 appliance 内部 thin metadata 预检并打印警告 | 最终镜像仍通过 `qemu-img check`，并在真实来宾内激活 thin pool、挂载 XFS、完成启动与关机 | 已用独立结构检查和运行门禁覆盖 |
| 本机缺少可用 Docker daemon，磁盘余量也不足以同时保留双架构全量构建 | 无法在 macOS 本机高效完成 x86_64 Stage1；x86_64 还会退化到 TCG | 使用独立 x86_64 Linux 构建机完成原生全量构建，本机只跑快速静态测试 | 已规避；最终验收仍需回到统一入口重放 |
| 国内环境访问部分上游慢或不稳定 | 固定源码偶发下载失败，直接改 URL 又会破坏源码身份 | `fetch` 支持传输层 URL 前缀重写，缓存按内容 SHA-256 寻址并在离线模式复验 | 已修复 |
| macOS 自带旧版 rsync 不支持 `--info=stats2` | 首次向构建机同步新增源码缓存时，命令在传输前退出 | 改用兼容的 `--stats`；随后 11 个文件一次同步并在远端按内容哈希复验 | 已规避；不属于项目或上游缺陷 |
| 目标程序验收命令曾把临时 wrapper 错认在 workspace 根目录 | 实际 wrapper 位于 build sysroot，首次 LVM 配置查询找不到文件 | 改为显式使用 Stage1 动态加载器和库目录执行目标 ELF；不再依赖错误路径假设 | 已纠正；不属于项目或上游缺陷 |
| Stage1 目标 ELF 在 Stage0 宿主中直接执行失败 | 两套 glibc/动态加载器不同；CMake、Coreutils、Bison、Linux host tools 等会在构建时执行刚生成的目标程序 | 统一生成目标加载器 wrapper，显式传入目标库搜索路径与 link-time `rpath-link`；x86_64 原 108 包全量及新增 12 包增量构建通过 | 已修复 |
| Stage1 的 xmake wrapper 在构建根外手动执行时找不到 Lua 模块目录并以 255 退出 | xmake 的模块位于锁定 channel 的 `share/xmake`，仅伪装 `argv[0]` 仍会受调用位置与缓存状态影响 | wrapper 在调用者未显式覆盖时设置构建 sysroot 内的 `XMAKE_PROGRAM_DIR`，并增加路径回归检查 | 已修复；属于本仓库工具隔离问题 |
| 构建系统把 sysroot 或工作目录写进最终包 | Meson/CMake 安装路径及部分生成文件使用了构建期绝对路径 | 修正 systemd、dbus、man-db、mkinitcpio、CMake 等配方和补丁；包归档扫描未发现构建根泄漏 | 已修复 |
| Linux 内核 `objtool` 及其库由目标工具链生成，宿主不能直接运行 | 内核构建在 objtool 阶段中断 | 为内核 host tools 写入 Stage1 解释器/RPATH，并只在 objtool 调用点使用目标运行器；x86_64 内核及 6,440 个模块构建通过 | 已修复 |
| Sage 归档遍历顺序不稳定，且 USTAR 头使用 GNU magic 与 POSIX prefix 语义混搭 | 同源码可能生成不同包哈希，长路径也可能被标准 tar 误解 | Sage 上游增加稳定排序、POSIX USTAR 头和双创建顺序回归测试；本仓库已把源码基线更新到合并提交 | 已修复并合并；[Sage PR #7](https://github.com/antinomie1/sage/pull/7) |
| Sage 修复在 GCC 15.3 通过、在 GitHub Actions 的 GCC 16 C++ Modules 下出现迭代器 ABI mangling 冲突 | GCC 16 的 Modules 实现会在排序 `vector<directory_entry>` 时重复实例化冲突符号 | 改为按相对路径键控的有序映射；GCC 15.3 测试 11/11、GCC 16 Actions 均通过 | 已修复；包含在 [Sage PR #7](https://github.com/antinomie1/sage/pull/7) |
| Sage 按根包优先顺序解包，并吞掉符号链接替换目录时的错误 | `base-files` 最后安装时无法把已有 `/sbin`、`/usr/sbin` 目录替换为 `/usr` 合并链接，却仍报告 120 包安装成功 | 按依赖优先顺序安装并严格检查解包错误；真实 120 包重建后六个合并路径均为预期符号链接 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| Sage 解包遇到后置路径冲突时会留下已写入但未登记的文件 | LMDB 事务尚未提交，但失败前的真实 rootfs 写入不会随事务回滚 | 写入前完整预检归档与目标的路径类型冲突；回归测试确认冲突后保留原文件且没有任何新文件落盘 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| Sage 第一遍预检跳过 manifest，第二遍写完 data 后才解析元数据 | 损坏 manifest 会让 rootfs 留下无 LMDB 记录的文件；直接包参数和 repo index 的“探测”也会执行真实解包 | 第一遍只读遍历完整 Zstandard/Tar、解析 manifest/service 并验证全部路径，第二遍才写；直接包与索引改用只读 inspect；损坏 manifest 回归确认零 payload 写入 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| `sage install A B` 对全部包共用一笔 LMDB 写事务 | A 已写盘后 B 预检失败会回滚 A 的数据库记录，使 A 变成幽灵文件 | orphan prune 独立提交，每个成功解包包各自提交 LMDB；A 成功、B 冲突的回归确认 A 的文件与记录同时保留，B 均不存在 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| Sage 只按包名保存归档路径 | 仓库同时存在多个版本时，solver 可能选中 2.0、实际解压 1.0，却把 LMDB 记录成 2.0；direct 1.0 也可能被仓库 2.0 的候选替换 | 归档按包名、版本/release、架构、channel 完整身份索引；direct 包锁定精确版本；解包器第二遍写入前再次核对所选身份；多版本、direct、错配归档和同包升级回归均通过 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| 两个 Sage 包包含同一普通文件时会静默覆盖 | 路径预检允许普通文件替换普通文件，`register_files()` 又直接覆盖 LMDB owner，先装包的文件和归属同时丢失 | 只读 inspect 收集真实 data 路径；在同一 LMDB 写事务内、解包前检查 owner，跨包冲突 fail closed，同包升级放行；注册函数自身再做原子复检 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| 新的文件所有权门禁揭示现有拆分包有 3,228 个非目录路径重叠 | `xz` 首先因 locale 与 `xz-libs` 重叠而中止；全量审计又发现 ncurses terminfo、PCRE2 文档/工具、util-linux/e2fsprogs 库文件及少量 man/config 冲突 | 按包职责收紧 14 个配方的 install 清理或重命名规则并提升 release；14 个冲突包重建后全仓审计只剩 Sage 特例 `usr/share/info/dir`；双架构全新 120 包 rootfs 均通过完整校验 | 配方、归档与最终 rootfs 均已修复 |
| Sage 每包提交后仍把激活、FHS profile 和 triggers 延迟到整组安装成功 | A 已完成文件和 LMDB 提交后，B 失败会直接返回，导致 A 成为“已安装但未后处理”的半成品 | 每个包提交后立即完成 toolchain 激活、profile 重建和该包 triggers；整组成功后保留聚合 trigger pass；A 成功、B 失败回归确认 A 的 profile 与激活链接存在 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| Sage extractor 接受 `..` data 路径，并可能沿归档内或既有符号链接逃出 `--root` | 恶意包可写入 sysroot 外部，且旧 probe 路径会在正式安装前触发风险 | 拒绝绝对路径、`..`、重复/不支持条目；拒绝归档 symlink 父路径并解析既有父链接的 root containment；三类越界回归均在写盘前失败 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| Sage 把文件名中的反斜杠原样写入 TOML，查询又静默跳过解析失败项 | systemd 的 `system-systemd\\x2d...` 路径破坏 LMDB 清单；部分成功列表还会让 install 把该包 ownership 当孤儿清理，`query info` 则误报未安装 | manifest 与 repo index 统一转义；installed 列表和单包读取均改为 `expected`，让 install/remove/query/rebuild fail closed；损坏 LMDB fixture 确认 ownership 不变并返回数据库损坏 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| macOS 本机与 Stage1 chroot 都不能直接复用 Sage CI 的完整测试工具集 | 本机缺 Linux `elf.h`；精简 chroot 缺 `bsdtar`，构建用 wrapper 又记录宿主绝对 sysroot 路径 | 本机只做 diff/static 检查；在 x86_64 Stage1 GCC 15.3 环境 clean build，并为测试临时提供 chroot 内 `bsdtar` loader wrapper；11/11 通过 | 已规避；不进入产物 |
| GitHub 的通用 `/archive/<commit>.tar.gz` 入口对 Sage 固定提交返回 404 | 固定提交存在，但下载入口无法作为可复现源码地址使用 | 改用 PR #8 merge commit `8629fdf` 的 codeload 内容地址并固定 SHA-256 `99a5fbda…530d` | 已规避；源码身份未改变 |
| Sage PR #8 合并后，本仓库仍复制并应用已上游化的临时补丁 | 新源码会重复应用相同改动，Stage0 与 Stage1 无法稳定重建 | Stage0 与 Stage1 同时固定 merge commit `8629fdf`，移除本地补丁及其测试假设；build 门禁 90/90 通过 | 已修复；[Sage PR #8](https://github.com/antinomie1/sage/pull/8) |
| 最新 `main` 的 Extra recipes 使用 `[[capability_hooks]]`，但 recipe validator 仍将其判为未知键 | `c49d13b` 的 GitHub Actions 失败，新增 grub/mkinitcpio 配方无法通过仓库自身门禁 | 校验器按 Sage 实际解析范围支持顶层和 `[package]` hooks，并校验 capability、exec、args；fixtures 39/39、全树 315/315 通过 | 已在本分支修复；待独立 PR 回补 `main` |
| 多架构分支与最新 `main` 分叉 15/58 个提交 | `main` 新增 Stage2/Extra 与 checksum 修复，直接继续会让最终合并积累冲突 | 将 `c49d13b` 合入功能分支；保留已验证的 Zen 源码，吸收官方 man-pages 镜像和零债务哨兵文件 | 已解决；未合并回 `main` |
| 构建机的 libguestfs/supermin 找不到可用于 appliance 的宿主内核 | `guestfish run` 无法启动，因而不能在无 device-mapper 权限的容器中组装真实磁盘布局 | 安装发行版 `linux-image-virtual` 后以 direct/TCG 后端完成 64 MiB qcow2 读写 smoke test | 已修复构建环境；不进入最终镜像 |
| systemd 包有 `bootctl`，但没有 systemd-boot EFI 文件 | 当前配方未启用 `efi`/`bootloader`，且缺少构建所需的 pyelftools | 增加固定源码的 pyelftools，并显式启用 EFI/bootloader 与 sclinux SBAT 元数据；双架构 EFI 文件均进入镜像并由 OVMF/AAVMF 启动 | 已修复并完成双架构复验 |
| systemd 从 Stage1 的 `dbus-1.pc` 读到带 sysroot 的绝对安装目录 | EFI 版 systemd 首次重建成功后，包路径审计发现 `pkg/root/.../build-sysroot/usr/share/dbus-1/services` | 显式固定 policy、session、system-service 与 interfaces 四个 D-Bus 目录，并增加回归测试；重建包路径正常 | 已修复；属于本仓库配方问题 |
| xfsprogs 选中宿主 GNU Make 4.3，无法继承 Stage1 GNU Make 4.4 的 FIFO jobserver | configure 优先查找 `gmake`，Stage1 wrapper 仅提供 `make`，于是回退到宿主 `/usr/bin/gmake` | 为 Stage1 make wrapper 提供同运行时的 `gmake` 别名；同时固定 XFS udev 规则目录，避免 pkg-config sysroot 路径进入包 | 已修复；属于本仓库构建隔离问题 |
| xfsprogs 首个成功包把 libhandle 安装到 x86 专用 `/usr/lib64`，并错误声明不存在的 `libxfs.so.0` | 上游默认启用 lib64 suffix；libxfs 是内部静态库，不是已安装共享 ABI | 禁用 lib64 suffix，统一安装到 `/usr/lib`，capability 改为实际存在的 `libhandle.so.1`；重建包内容与声明一致 | 已修复；属于本仓库配方问题 |
| efivar 构建时直接运行目标 ELF `makeguids` | 目标程序需要 Stage1 glibc 2.44，Ubuntu 宿主 glibc 版本较旧 | 通过固定补丁让 makeguids 使用架构对应动态加载器和 Stage1 库路径执行 | 已修复；x86_64 重建通过 |
| efibootmgr 默认 `-flto` 导致 lto-wrapper 绕过 GCC wrapper | LTO 子进程直接启动 Stage1 原始 GCC，再次落到宿主 glibc | 此小工具不依赖 LTO 语义，配方显式使用 `-O2 -g` 关闭 LTO并保留正常优化 | 已修复；x86_64 重建通过 |
| LVM2 在缺少 cache/thin 元数据工具时仍推断出不存在的 `/usr/sbin/cache_*` 路径 | 运行时配置会声称存在未打包的检查和修复工具；thin 路径已留空但 cache 路径未固定 | 配方显式把两类外部工具的 check、dump、repair、restore 路径全部留空；目标 ELF 配置查询已确认八个路径为空，双架构真实启动均激活 thin pool | 已修复并完成运行验收 |
| `stage1-run` 直接读取旧的 rendered recipe，canonical recipe 更新后仍可能成功打出旧 release | 增量重建不会自动刷新工作区，退出码无法证明新配方已经进入产物 | 运行前逐文件比较 canonical 渲染结果与工作区 recipe/helper；不一致时拒绝构建并提示重跑 `stage1-recipes` | 已修复；属于本仓库增量构建正确性问题 |
| `stage1-run --workspace` 接受相对路径但原样传给会切换工作目录的 Sage | prepare 进入源码目录后，相对 `$RECIPE_DIR` 指向错误位置，release 10 首次增量重建在应用补丁前退出 | `run_stage1_packages` 在读取 recipes/sources 前统一解析 workspace 与显式 sysroot；新增相对路径回归门禁，并用同一相对命令重跑 | 已修复；属于本仓库路径规范化问题 |
| 目标 sysroot 原生重建时把 `/` 本身当成禁止泄漏路径 | `--sysroot /` 使任意 payload 和正常 `#!/usr/bin/*` shebang 都被误判，已完成的 `file-libs` 包在归档后仍失败 | 路径与 shebang 校验仅跳过无信息量的根目录，仍严格检查 workspace；新增原生根回归并在目标 glibc chroot 重建 | 已修复；属于本仓库原生自举边界问题 |
| 临时目标 chroot 未挂载 `/dev`，Tcl configure 把 `/dev/null` 创建成普通文件 | 本应丢弃的 cache diff 被 shell 当作空 heredoc 输入写回 `config.status`，表现为脚本混入 `0a1,63` 后语法错误 | 删除污染的普通文件，并只在临时验收 chroot 建立标准 null/zero/random/urandom 字符设备；保留配方原并行语义 | 已规避；属于手工验收环境缺陷，不进入产物 |
| 交付 initramfs 使用 `autodetect` 会按云构建机硬件裁剪驱动 | 产物可能漏掉 UTM/QEMU 所需的 IDE、virtio 或架构特定存储模块，构建成功仍无法挂载根卷 | 交付配置移除 `autodetect`，由 `block`、`lvm2`、`filesystems` hook 收入可移植模块集；双架构 initramfs 内容检查与真实启动均通过 | 已修复并完成双架构复验 |
| 启动包的卷组名和 ESP 默认挂载点偏离安装规范 | 内核参数寻找 `/dev/mapper/sclinux-root`，镜像规范实际创建 `vg0/root`；更新脚本默认写 `/efi` 而规范挂载 `/boot/efi` | 统一为 `root=/dev/mapper/vg0-root rd.lvm.lv=vg0/root` 和 `/boot/efi`，并增加双架构渲染回归检查 | 已修复；属于本仓库启动契约问题 |
| mkinitcpio 已带 LVM2 hook，但基础包集此前缺少 LVM2 与 XFS 用户态工具 | 无法生成可激活 XFS-on-LVM-thin 根卷的 initramfs | 已补齐 LVM2、XFS 与镜像工具配方；双架构均为 120 个唯一包，并在真实启动中激活 XFS-on-LVM-thin 根卷 | 包闭包与运行验收均已完成 |
