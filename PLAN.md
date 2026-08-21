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
| D：可启动系统包集 | 进行中（x86_64 Stage1 已完成 120/120，包归档已核对；待 rootfs 中生成并检查真实 initramfs） |
| E：rootfs/qcow2 组装 | 未开始 |
| F：x86_64 验证 | 未开始 |
| G：aarch64 验证 | 未开始 |
| H：文档、CI 与交付 | 未开始 |

每完成一个阶段，应在本表更新状态，并保留对应提交和可复查的测试结果。

## 8. 实施问题记录

这里记录实施过程中实际遇到的问题、处理状态和上游跟踪；它是计划执行记录，不是 CHANGELOG。

| 问题 | 影响与根因 | 处理与验证 | 状态 / 上游 |
| --- | --- | --- | --- |
| 本机缺少可用 Docker daemon，磁盘余量也不足以同时保留双架构全量构建 | 无法在 macOS 本机高效完成 x86_64 Stage1；x86_64 还会退化到 TCG | 使用独立 x86_64 Linux 构建机完成原生全量构建，本机只跑快速静态测试 | 已规避；最终验收仍需回到统一入口重放 |
| 国内环境访问部分上游慢或不稳定 | 固定源码偶发下载失败，直接改 URL 又会破坏源码身份 | `fetch` 支持传输层 URL 前缀重写，缓存按内容 SHA-256 寻址并在离线模式复验 | 已修复 |
| macOS 自带旧版 rsync 不支持 `--info=stats2` | 首次向构建机同步新增源码缓存时，命令在传输前退出 | 改用兼容的 `--stats`；随后 11 个文件一次同步并在远端按内容哈希复验 | 已规避；不属于项目或上游缺陷 |
| 目标程序验收命令曾把临时 wrapper 错认在 workspace 根目录 | 实际 wrapper 位于 build sysroot，首次 LVM 配置查询找不到文件 | 改为显式使用 Stage1 动态加载器和库目录执行目标 ELF；不再依赖错误路径假设 | 已纠正；不属于项目或上游缺陷 |
| Stage1 目标 ELF 在 Stage0 宿主中直接执行失败 | 两套 glibc/动态加载器不同；CMake、Coreutils、Bison、Linux host tools 等会在构建时执行刚生成的目标程序 | 统一生成目标加载器 wrapper，显式传入目标库搜索路径与 link-time `rpath-link`；x86_64 原 108 包全量及新增 12 包增量构建通过 | 已修复 |
| 构建系统把 sysroot 或工作目录写进最终包 | Meson/CMake 安装路径及部分生成文件使用了构建期绝对路径 | 修正 systemd、dbus、man-db、mkinitcpio、CMake 等配方和补丁；包归档扫描未发现构建根泄漏 | 已修复 |
| Linux 内核 `objtool` 及其库由目标工具链生成，宿主不能直接运行 | 内核构建在 objtool 阶段中断 | 为内核 host tools 写入 Stage1 解释器/RPATH，并只在 objtool 调用点使用目标运行器；x86_64 内核及 6,440 个模块构建通过 | 已修复 |
| Sage 归档遍历顺序不稳定，且 USTAR 头使用 GNU magic 与 POSIX prefix 语义混搭 | 同源码可能生成不同包哈希，长路径也可能被标准 tar 误解 | 本仓库先带固定补丁；Sage 上游 PR 增加稳定排序、POSIX USTAR 头和双创建顺序回归测试 | 已修复；[Sage PR #7](https://github.com/antinomie1/sage/pull/7) |
| Sage 修复在 GCC 15.3 通过、在 GitHub Actions 的 GCC 16 C++ Modules 下出现迭代器 ABI mangling 冲突 | GCC 16 的 Modules 实现会在排序 `vector<directory_entry>` 时重复实例化冲突符号 | 改为按相对路径键控的有序映射；GCC 15.3 测试 11/11、GCC 16 Actions 均通过 | 已修复；包含在 [Sage PR #7](https://github.com/antinomie1/sage/pull/7) |
| systemd 包有 `bootctl`，但没有 systemd-boot EFI 文件 | 当前配方未启用 `efi`/`bootloader`，且缺少构建所需的 pyelftools | 增加固定源码的 pyelftools，并显式启用 EFI/bootloader 与 sclinux SBAT 元数据；x86_64 包已核对 `systemd-bootx64.efi` | 已修复；aarch64 对应文件待该架构构建复验 |
| systemd 从 Stage1 的 `dbus-1.pc` 读到带 sysroot 的绝对安装目录 | EFI 版 systemd 首次重建成功后，包路径审计发现 `pkg/root/.../build-sysroot/usr/share/dbus-1/services` | 显式固定 policy、session、system-service 与 interfaces 四个 D-Bus 目录，并增加回归测试；重建包路径正常 | 已修复；属于本仓库配方问题 |
| xfsprogs 选中宿主 GNU Make 4.3，无法继承 Stage1 GNU Make 4.4 的 FIFO jobserver | configure 优先查找 `gmake`，Stage1 wrapper 仅提供 `make`，于是回退到宿主 `/usr/bin/gmake` | 为 Stage1 make wrapper 提供同运行时的 `gmake` 别名；同时固定 XFS udev 规则目录，避免 pkg-config sysroot 路径进入包 | 已修复；属于本仓库构建隔离问题 |
| xfsprogs 首个成功包把 libhandle 安装到 x86 专用 `/usr/lib64`，并错误声明不存在的 `libxfs.so.0` | 上游默认启用 lib64 suffix；libxfs 是内部静态库，不是已安装共享 ABI | 禁用 lib64 suffix，统一安装到 `/usr/lib`，capability 改为实际存在的 `libhandle.so.1`；重建包内容与声明一致 | 已修复；属于本仓库配方问题 |
| efivar 构建时直接运行目标 ELF `makeguids` | 目标程序需要 Stage1 glibc 2.44，Ubuntu 宿主 glibc 版本较旧 | 通过固定补丁让 makeguids 使用架构对应动态加载器和 Stage1 库路径执行 | 已修复；x86_64 重建通过 |
| efibootmgr 默认 `-flto` 导致 lto-wrapper 绕过 GCC wrapper | LTO 子进程直接启动 Stage1 原始 GCC，再次落到宿主 glibc | 此小工具不依赖 LTO 语义，配方显式使用 `-O2 -g` 关闭 LTO并保留正常优化 | 已修复；x86_64 重建通过 |
| LVM2 在缺少 cache/thin 元数据工具时仍推断出不存在的 `/usr/sbin/cache_*` 路径 | 运行时配置会声称存在未打包的检查和修复工具；thin 路径已留空但 cache 路径未固定 | 配方显式把两类外部工具的 check、dump、repair、restore 路径全部留空；目标 ELF 配置查询已确认八个路径为空 | 配置已修复；真实 LVM thin 激活待启动验收 |
| `stage1-run` 直接读取旧的 rendered recipe，canonical recipe 更新后仍可能成功打出旧 release | 增量重建不会自动刷新工作区，退出码无法证明新配方已经进入产物 | 运行前逐文件比较 canonical 渲染结果与工作区 recipe/helper；不一致时拒绝构建并提示重跑 `stage1-recipes` | 已修复；属于本仓库增量构建正确性问题 |
| 交付 initramfs 使用 `autodetect` 会按云构建机硬件裁剪驱动 | 产物可能漏掉 UTM/QEMU 所需的 IDE、virtio 或架构特定存储模块，构建成功仍无法挂载根卷 | 交付配置移除 `autodetect`，由 `block`、`lvm2`、`filesystems` hook 收入可移植模块集；真实内容待 rootfs 阶段检查 | 已修复配置；待 initramfs 与双虚拟机启动复验 |
| mkinitcpio 已带 LVM2 hook，但基础包集此前缺少 LVM2 与 XFS 用户态工具 | 无法生成可激活 XFS-on-LVM-thin 根卷的 initramfs | 已补齐 LVM2、XFS 与镜像工具配方，x86_64 锁文件为 120 个唯一包；继续以 initramfs 内容检查和真实启动验证收口 | 包闭包已修复；真实 initramfs 待 rootfs 阶段验证 |
