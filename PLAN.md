# ShenChen Linux 独立 recipes / Recipedia 迁移计划

**状态：** 待执行

**工作分支：** `build/reproducible-multiarch`

**运行基线：** 双架构 120 包 rootfs/qcow2 已完成；历史构建证据见
[`docs/BUILD_REPORT.md`](docs/BUILD_REPORT.md)。

## 1. 独立仓库迁移目标

2026-08-22 的仓库拆分改变了本计划的长期边界：

- [`sclinuxdev/recipes`](https://github.com/sclinuxdev/recipes) 是发行版配方的唯一权威来源；
- [`sclinuxdev/recipedia`](https://github.com/sclinuxdev/recipedia) 展示配方状态、接收二进制包、生成 Sage 仓库索引并保存构建日志；
- [`sclinuxdev/sclinux`](https://github.com/sclinuxdev/sclinux) 保留 Stage0、bootstrap 包集合、构建编排、rootfs、qcow2 和 QEMU 启动门禁；
- [`sclinuxdev/sage`](https://github.com/sclinuxdev/sage) 负责包身份、架构兼容、安装事务和仓库客户端语义。

迁移后的数据流为：

```text
recipes（配方唯一来源）
        │
        ▼
SCLinux 构建器（x86_64 / aarch64）
        │
        ▼
Recipedia（二进制包、index.toml、状态、日志）
        │
        ▼
SCLinux（rootfs → qcow2 → QEMU 门禁 → Actions artifact）
```

Stage0 仍由 SCLinux 管理。Stage1 与 Stage2 不再各自保存长期分叉的完整配方树；
它们表示使用同一份 recipes 时所处的自举阶段和构建上下文。

## 2. 迁移原则与边界

1. **固定身份后再构建。** SCLinux 必须固定 recipes 的精确提交，不使用浮动 `main`。
2. **迁移修复，不覆盖新版本。** 旧 Stage1 已验证修复逐项移植到 recipes 的最新版本，
   不把旧 recipe 整份复制到新仓库。
3. **配方与编排分离。** 通用 configure/install 修复进入 recipes；Stage0 sysroot、
   target runner、缓存、wrapper 和 QEMU 参数留在 SCLinux。
4. **包身份必须真实。** 包文件名、manifest、索引和 ELF 架构必须一致；
   `any` 只用于真正架构无关的 payload。
5. **先闭合可启动集合，再扩到完整目录。** PR 门禁先构建 bootable closure，
   定时或发布流水线再构建完整 recipes 集合。
6. **旧产物只作回归基线。** 已验证的 120 包 rootfs/qcow2 用来比较行为，
   不能代替从最新 recipes 的全新构建。
7. **删除最后发生。** 只有新链路通过双架构端到端门禁后，才删除 SCLinux
   中重复的 Stage1/Stage2 配方。

明确不迁入 recipes 的内容：

- `Stage0/`、`config/architectures.toml`、`tools/build.py`；
- `SC_BUILD_SYSROOT`、动态加载器 wrapper、PROOT 兼容处理；
- rootfs/qcow2 组装脚本、QEMU expect 测试和云构建机配置；
- `/distro/sage`、旧 GCC 15 channel 路径及其他只服务旧 Stage1 的临时绕行。

## 3. 分仓库实施计划

### 3.1 Sage：多架构包身份

工作内容：

- recipe 未声明 `arch` 时由构建目标提供精确架构，禁止继续静默默认成 x86_64；
- 保留 `arch = "any"`，并限制它只用于无 ELF 的架构无关包；
- 安装前校验目标系统与包架构兼容；
- 统一校验文件名、归档 manifest 和 solver 选中身份；
- 增加 x86_64、aarch64、`any` 与架构不匹配安装回归测试。

过渡期内，SCLinux 构建器仍会给临时 recipe 写入明确 `arch`，不依赖 Sage 默认值。

通过条件：

- aarch64 ELF 不会生成 `*-x86_64.pkg.tar.zst`；
- 不兼容架构包在写 rootfs 前失败；
- 同版本两架构归档可同时存在并被正确选择。

### 3.2 recipes：配方门禁

新增独立 CI，至少验证：

- 递归发现所有 `recipe.toml`，忽略的只有真实构建产物目录；
- TOML schema、字段类型、未知键和 64 位 SHA-256；
- 目录名严格等于 `name-version-release`；
- 包身份不重复，依赖与 `provides` 闭包完整；
- `arch` 只能是 `x86_64`、`aarch64` 或 `any`；
- recipe 引用的本地配置、补丁和脚本全部存在；
- 通用 recipe 不含未声明的 x86_64 专用路径；
- install 不向 `/sbin`、`/usr/sbin`、`/lib64` 或 `/usr/lib64` 写普通文件。

通过条件：validator fixture 与全树验证都由 GitHub Actions 从干净 clone 通过。

### 3.3 recipes：通用修复迁移

按最新 recipe 版本逐项移植：

- binutils target triplet；
- GMP 的 x86_64 `--build` 硬编码；
- libffi 的 `native` CPU 优化；
- glibc 动态加载器 capability；
- Sage 的 `build/linux/x86_64` 安装路径和编译 baseline；
- Linux Zen 的 aarch64 config、`Image` 路径和 mkinitcpio preset；
- GRUB 的 x86_64-only 身份；
- systemd 的 x64/AA64 EFI bootloader；
- base-files 的 `systemd-oom` 用户与组；
- 已在 120 包构建中验证的 info 目录、拆包文件归属、`/usr` merge、
  运行时路径和宿主污染修复。

每次改变已发布 recipe 必须提升 release，并同步重命名 `name-version-release` 目录。

### 3.4 recipes：启动包闭包

按当前拆包规则补齐或核对：

- `shc`；
- `python-pyelftools`；
- `popt` / `popt-dev`；
- `libaio` / `libaio-dev`；
- `libunistring` / `libunistring-dev`；
- `lvm2`；
- `efivar` 的 CLI、运行库和开发文件；
- `efibootmgr`、`gptfdisk`；
- `sclinux-boot`。

同时更新 `base`、`base-devel`、Linux、mkinitcpio、LVM2、systemd 与
systemd-boot 的依赖和 capability trigger。启动入口按架构选择：

| 架构 | systemd-boot | UEFI 默认入口 | 内核串口 |
| --- | --- | --- | --- |
| x86_64 | `systemd-bootx64.efi` | `BOOTX64.EFI` | `ttyS0` |
| aarch64 | `systemd-bootaa64.efi` | `BOOTAA64.EFI` | `ttyAMA0,115200` |

### 3.5 Recipedia：双架构发布一致性

工作内容：

- 同一 `name/version/release/channel` 同时保存 x86_64 与 aarch64 归档；
- 状态页按架构呈现 `missing`、`outdated`、`built`、`ahead`；
- 上传时校验归档文件名与 manifest 完整身份一致；
- 文件落盘、SQLite 更新和 `index.toml` 重建失败时回滚；
- unpublish 保持数据库、文件和索引一致；
- 并发 publish 使用锁或唯一临时文件，避免固定 `.index.toml.tmp` 竞争。

通过条件：连续或并发发布两种架构后，两个归档都可由 Sage 索引和安装，
任何中途失败都不留下孤儿文件或幽灵数据库记录。

### 3.6 SCLinux：消费独立 recipes

新增 recipes 锁文件，至少包含仓库 URL、commit 和可选 archive SHA-256。
构建器需要：

- 从干净 checkout 获取锁定 recipes；
- 禁止静默回退到 `Stage1/recipes`；
- 维护 bootable package set，而不是复制另一份 recipe；
- 从 `dependencies`、`build_dependencies` 和 `provides` 生成或验证拓扑；
- 为目标架构渲染临时 recipe，并保留 canonical recipe 哈希；
- 构建日志记录 recipes commit、Sage commit、源码哈希和包哈希；
- 从 Recipedia 仓库重新安装 rootfs，避免仅验证构建目录中的临时包。

通过条件：移动或隐藏 SCLinux 内置 recipe 树后，锁定的外部 recipes 构建仍能完成。

### 3.7 GitHub Actions：完整交付链

快速 PR 门禁：

1. recipes schema、依赖闭包和架构静态检查；
2. Stage0/构建器单元测试；
3. 两架构 bootable closure 构建或可复用的分层 smoke；
4. 包路径冲突、ELF machine、宿主路径和未登记文件检查。

发布/定时门禁：

1. 从空缓存构建 x86_64 与 aarch64 包；
2. 上传包与日志到 Recipedia；
3. 从 Recipedia 安装两个 rootfs；
4. 生成两个 16 GiB UEFI qcow2；
5. `qemu-img check`；
6. OVMF/AAVMF 启动到 `multi-user.target`；
7. Sage verify、SHC、XFS-on-LVM、failed unit 和正常关机门禁；
8. 上传 rootfs、qcow2、`SHA256SUMS`、`build-info.json` 和串口日志。

## 4. 迁移执行顺序

| 顺序 | 仓库 / 交付 | 完成定义 |
| --- | --- | --- |
| 1 | recipes 原型审计 | 现有未提交原型逐项保留或撤销，没有未经验证的旧 Stage1 绕行 |
| 2 | Sage 架构 PR | 包身份与安装兼容测试通过 |
| 3 | recipes CI PR | 全树 schema、SHA、目录和闭包门禁通过 |
| 4 | recipes 多架构/启动 PR | bootable closure 在两架构完成真实构建 |
| 5 | Recipedia PR | 双架构 publish/index/install 与失败回滚通过 |
| 6 | SCLinux 消费外部 recipes | 固定 commit 构建，不回退内置配方 |
| 7 | SCLinux Actions | rootfs、qcow2、双 QEMU 门禁和 artifacts 自动完成 |
| 8 | 清理重复配方 | 端到端门禁保持通过后删除 Stage1/Stage2 重复 recipe |

各仓库使用独立分支和 PR；不直接合并 `main`。仓库内容、commit 与 PR 使用英文
Conventional Commit。由 Codex 创建的提交必须带：

```text
Co-authored-by: Codex <codex@openai.com>
```

不创建 CHANGELOG。

## 5. 迁移验收标准

迁移完成必须同时满足：

1. recipes 是唯一长期配方来源，SCLinux 不保留另一份完整配方树；
2. 通用 recipe 不含未声明的 x86_64 假设；
3. 两架构包名、版本和 release 集合一致，显式单架构包除外；
4. 所有 ELF machine、动态加载器、内核和 EFI 文件符合目标架构；
5. 跨包普通文件冲突为 0，rootfs 未登记文件为 0；
6. Recipedia 可同时保存、索引和安装同版本的两个架构；
7. x86_64 与 aarch64 均从干净 recipes checkout 构建；
8. 两个 qcow2 均通过 UEFI、LVM thin、XFS、systemd、Sage、SHC、
   零 failed unit 和正常关机门禁；
9. GitHub Actions 可从干净 clone 生成可下载 rootfs、qcow2、校验和与日志；
10. 旧 120 包镜像继续作为行为回归基线，但不再被当作新配方构建成功的证据。

## 6. 迁移风险与停止条件

| 风险 | 处理方式 / 停止条件 |
| --- | --- |
| 最新 recipes 使用 GCC 16，而已验证镜像使用 GCC 15.3 | 先构建工具链 slice；GCC 16 自举或运行库不闭合时停止扩大包集 |
| 192 个新配方与已验证 120 包拆分不同 | 先闭合 bootable set，再跑完整目录；不以包数量相等代替依赖和文件验收 |
| recipe shell 根据宿主 `uname` 误判交叉目标 | 架构身份由构建器显式提供；发现宿主/目标混用即停止该包迁移 |
| Recipedia 发布不是文件系统与数据库原子事务 | 原子性回归通过前不把它作为唯一发布源 |
| 删除 SCLinux 内置 recipe 后回滚困难 | 删除单独提交，且必须晚于外部 recipes 双架构门禁 |
| Actions 构建时间或存储超限 | PR 跑 bootable closure，完整目录放定时/发布任务；缓存只加速，不作为输入来源 |

## 7. 当前迁移状态

| 项目 | 状态 |
| --- | --- |
| 双架构 120 包运行基线 | 已完成并保留最终哈希与启动证据 |
| 最新 recipes / Recipedia / Sage 远端盘点 | 已完成（2026-08-22 快照；执行前仍需刷新） |
| recipes 迁移分支 | `fix/migrate-multiarch-recipes`，存在未提交原型，未推送 |
| recipes 原型审计 | 待执行 |
| Sage 多架构包身份 | 待实现 |
| recipes 独立 CI | 待实现 |
| recipes bootable closure | 待实现 |
| Recipedia 双架构发布一致性 | 待实现 |
| SCLinux 外部 recipes 消费 | 待实现 |
| 双架构 Actions rootfs/qcow2 | 待实现 |
| 删除重复 Stage1/Stage2 recipes | 未开始，必须最后执行 |
