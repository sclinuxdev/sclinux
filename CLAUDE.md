# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**本文件优先级最高。** 与 `docs/` 下任何文档冲突时，以本文件为准 —— 那些文档
记录的是当时的结论，本文件记录的是现在的口径。发现出入不要「修正」本文件去
迁就文档，也不要擅自改文档，先问。

---

## 0. 不要读的文件

以下两个文件是玩笑内容，与工程无关，**不要读取、不要引用、不要据其推断任何设计**：

- `README.md`
- `docs/浩宸宇宙_狼王与信号场设定集.md`

真实的设计依据只有 `docs/DISTRO_POLICY.md`、`docs/SAGE_DESIGN.md`、
`docs/INSTALLATION.md`、`package-split-channel.md`。

---

## 1. 这个仓库是什么

**ShenChen Linux (sclinux)** —— 一个从零自举的 x86_64 Linux 发行版。
本仓库存放**配方与政策**，不存放源码 tarball，也不存放二进制包。

| 路径 | 内容 |
| :--- | :--- |
| `Stage1/manifest.toml` | 102 包的拓扑构建清单（`batch` 仅为设计分组标记） |
| `Stage1/recipes/` | 107 个 stage1 配方（在 stage0 chroot 内全量重建） |
| `Stage2/recipes/` | 178 个 stage2 配方（细粒度拆分 + 完整工具链，原生自举） |
| `Stage2/build-stage2.sh` | stage2 构建引擎，内嵌 139 项 `BUILD_ORDER` 拓扑序 |
| `Stage2/_gen_recipes.py` | stage2 配方生成器；**拆分策略的权威实现在此文件的 docstring** |
| `packages/` | 发行版自有包（目前只有 `shc`） |
| `scripts/shc` | `sage` 的简写前端，装为 `/usr/bin/shc` |
| `tests/` | CI 校验器（配方 schema、markdown 链接、shc 行为） |

包管理器 `sage` 本身是**上游独立项目**（<https://github.com/antinomie1/sage>，
BSD-2-Clause），不在本仓库内；本仓库是 BSD-3-Clause。

---

## 2. 常用命令

```bash
# —— CI 的全部四项检查，改动后必须本机跑一遍 ——
sh      tests/test-shc.sh                 # shc 别名展开行为
shellcheck -s sh scripts/shc tests/test-shc.sh
python3 tests/test-validate-recipes.py    # 校验器自身的 fixture
python3 tests/validate-recipes.py         # 全树 recipe schema
python3 tests/test-check-links.py
python3 tests/check-links.py              # markdown 链接与锚点

# 只跑一个 shc 用例：test-shc.sh 无过滤参数，直接看输出定位
# 清理已补齐校验和的债务条目
python3 tests/validate-recipes.py --update-debt

# —— 构建单个包 ——
sage build ./packages/shc
sage --root /tmp/sctest install shc       # 永远不要拿 / 做试验
```

`validate-recipes.py` 是**递归发现**的：任何新增的 recipe 树自动纳入校验，
无法绕过。它跳过 `pkg/`、`src/`、`distfiles/`（`sage build` 的产物目录）。

---

## 3. 校验和是硬约束

`recipe.toml` 有 `[source] url` 就**必须**有非空 `sha256`。
sage 在 `sha256` 缺失时**直接跳过校验**使用下载内容，等于信任任意中间人。

`tests/checksum-debt.txt` 曾登记历史遗留的 102 个 Stage1 配方，按路径钉死；
这些债务现已全部清偿，文件为空。校验器对新增条目的态度是明确的：

> a newly added source must ship a checksum -- do not add it to checksum-debt.txt

**当前状态**：全树 286 个配方均通过 schema 校验；凡有 `[source] url` 的配方
都已有非空 `sha256`，没有待清偿的校验和债务。

---

## 4. sage 机制要点

读源码或散落文档才能拼出来的部分，集中在此。

### 4.1 `recipe.toml` 解析

`dependencies` / `build_dependencies` / `provides` / `prepare` / `build` / `install`
这六个数组，sage 从**三个作用域合并**：顶层、`[package]`、`[source]`。
标量元数据只从 `[package]` 读，`url`/`sha256` 只从 `[source]` 读。

未知键被**静默忽略**——所以校验器把未知键当 typo 报错。
非字符串标量（`release = 1`）也不报错，而是回退到默认值，配方会用作者没写过的
值构建；校验器同样拦截。

构建阶段以 `sh` 执行，导出：

| 变量 | 值 |
| :--- | :--- |
| `DESTDIR` / `PKGDIR` | `<RECIPE_DIR>/pkg`，**安装产物必须落这里** |
| `PREFIX` | 固定 `/usr` |
| `SRCDIR` | `<RECIPE_DIR>/src` |
| `RECIPE_DIR` | 配方目录 |

工作目录：有 `src/` 时是 `src/`，否则是配方目录本身
（无 `[source]` 的配方直接在自己目录下跑，见 `packages/shc`）。

### 4.2 包格式

```
{name}-{version}-{release}-{arch}.pkg.tar.zst
├── .METADATA/manifest.toml    # 元数据、provides、依赖
├── .METADATA/service.toml     # 可选，通用守护进程定义
└── data/                      # 直接映射落盘（usr/bin/... etc/...）
```

**`arch` 必须在文件名里**：`index.toml` 记录 `arch` 但不记录文件名，安装端从
元数据反推名字，缺了它两种架构的同名包会静默互相覆盖。

打包时自动扫描 `data/` 下的 ELF：`DT_NEEDED` → `so:` 运行时依赖，
`DT_SONAME` → 本包 provides。**这个扫描不会把包自己安装的 `lib*.so*` 文件名
写进 provides**，是 Stage2 索引大量悬空约束的根源（见 §7）。

### 4.3 已实现能力

以下能力已在 sage 源码中实现（2026-08-21）：

- **`files.idx`**（`.METADATA/files.idx`）：逐文件 type/mode/size/sha256/path/target，
  在解包时校验。`sage query files <PKG>` 可列出已安装包的文件清单。
- **包自带 `triggers.toml`**（`.METADATA/triggers.toml`）：包可声明 `[[triggers]]`，
  通过 `on_paths` 前缀匹配或 `on_capability` 触发，执行 `exec` 或通过活动提供者的
  `[[capability_hooks]]` 解析 `run_capability`。按 `priority` 排序，同一事务内去重。
- **能力驱动触发器**：`[[capability_hooks]]` 在配方中声明某个 `capability` 的
  调用方式（`exec` + `args`），触发器通过 `run_capability = "virtual/..."` 间接调用，
  不必硬编码路径。initramfs 与引导器触发器已实现（`virtual/initramfs-generator` /
  `virtual/bootloader`）。
- **CLI 通道管理**：`sage channel add <NAME> <PATH>` / `sage channel sync <NAME>` /
  `sage install --channel <NAME> <PKG>` 均已实现。`sage channel list` 列出已配置通道。
- **构建期自校验**：`sage build` 扫描产物 ELF 的 `DT_SONAME` 写入 `provides`，
  校验 `DT_NEEDED` 能否被已声明的 `build_dependencies` 满足，在打包前拦截缺失依赖。

**两级能力模型**：

- **排他能力**（exclusive）：同一能力最多一个提供者安装，切换是重配置操作
  （`sage rebuild` 更新 `/var/lib/sage/system.toml [providers]` 并重跑触发器）。
  例：`virtual/init`、`virtual/udev`、`virtual/libc`。
- **共存能力**（shared）：提供者可共存，`[providers]` 仅记录求解器的默认选择，
  不排他。例：`virtual/kernel`、`virtual/initramfs-generator`、所有 `so:*`。

排他性是**选入**的：`/etc/sage/capabilities.toml` 的 `[capabilities.<name>]` 加
`exclusive = true`。未声明的能力默认共存。

### 4.4 `--root` 时的路径推导

`sage --root /mnt` 会把配置与状态挪到目标根：
`/mnt/etc/sage/channels.toml`、`/mnt/var/lib/sage/data.mdb`。
**目标根没有 `channels.toml` 时会回退读宿主的 `/etc/sage/channels.toml`** ——
装机脚本必须显式写目标根的那份，否则会静默用错仓库。

### 4.5 Channel

目录结构就是 `index.toml` + 一堆 `*.pkg.tar.zst`，本地 `file://` 即可用。
`sage repo index <DIR> <CHANNEL>` 生成索引。
**包归档与 `index.toml` 必须来自同一次构建**，不能混用旧索引或旧包。

四层 scope：`/`（system）、`/usr/lib/runtimes/`（runtime）、
`/opt/channels/`（toolchain）、`~/.local/`（user）；
Profile 引擎聚合成 `/etc/sage/profiles/default/{bin,lib,runtimes}` 并生成
`/etc/profile.d/sage-channels.sh`。**工具链装在 `/opt/channels/`，
所以 `gcc` 不在 `/usr/bin` 下，只在 login shell 的 PATH 里可见。**

### 4.6 LMDB 状态表（`/var/lib/sage/data.mdb`）

`packages`（名→元数据）、`files`（相对路径→`pkg:channel`，用于反查与冲突检测）、
`provides`（符号→包）、`channels`、`system`（`virtual/init` → 活动提供者）。

---

## 5. 包拆分规则

权威表述见 `package-split-channel.md` 与 `Stage2/_gen_recipes.py` 的 docstring。

1. **纯库**（无独立 CLI 产品）：`name`（SONAME 运行时）+ `name-dev`。
   **不产生 `name-libs`。** 例：zlib、lmdb、gmp、mpfr、readline、libffi、mpdecimal。
2. **库 + 独立 CLI 产品**：三分 `name`（程序）/ `name-libs`（SONAME）/ `name-dev`。
   例：xz、zstd、curl、openssl、sqlite、util-linux、e2fsprogs、python、perl。
3. **大型组件按功能拆，不按文件类型拆**：
   systemd → `systemd-libs` / `systemd-udev` / `systemd` / `systemd-networkd`
   / `systemd-resolved` / `systemd-timesyncd` / `systemd-dev`。
4. **构建工具保持完整**：libtool 把运行库、headers、macros、pc、CLI 合在一起；
   autoconf / automake 同理，不造人工的 `-libs`/`-dev` 兄弟包。
5. **虚拟能力**：实现包同时 provide 自身名与稳定 capability。
   `systemd`→`virtual/init`；`systemd-udev`→`virtual/udev`；
   `linux-zen`→`linux`/`virtual/linux`/`virtual/kernel`；
   `linux-zen-headers`→`linux-headers`/`virtual/linux-headers`。
6. **Meta 包**只聚合依赖、不含文件（`base` 就是，172 条依赖）。

虚拟提供者**只用于真正互斥的底座**：`virtual/init`、`virtual/udev`、`virtual/libc`。
内核、shell、awk、coreutils 是天然共存组件，按普通包管理，不要给它们造虚拟接口。

---

## 6. 发行版政策（改动代价）

「极高」= 仓库全部二进制包作废、必须整体重编。

`docs/DISTRO_POLICY.md` 有一份更长的版本，但**下表与它有出入且以下表为准**：
根文件系统与引导器不再作为已定选型，配方来源也不再硬性规定。

| 项 | 决定 | 代价 |
| :--- | :--- | :--- |
| `/usr` merge | `/bin` `/sbin` `/lib` `/lib64` 全部软链到 `/usr/*` | **极高** |
| libc | glibc | **极高**（musl 需并存第二套包集，机制支持但不提供） |
| init | systemd | **低**（`service.toml` 与 init 解耦，`sage rebuild` 重生成脚本） |
| 架构 | x86_64 单一 | 高 |
| 发布模式 | 滚动，不做定版 | 高 |

配方来源不作硬性规定。Arch 的 PKGBUILD 可以拿来当参考 —— 它的 configure
参数、补丁集与编译选项是社区长期踩坑的产物，`*.pkg.tar.zst` 也本就沿用
Arch 的约定。但这是可选路径，从上游源码直接写配方同样成立。

真要参考 PKGBUILD 时：保留出处标注；核对 Arch 补丁是否仍适用；
**不要照搬依赖列表**，本项目的拆分粒度与 Arch 不同。

`/usr` merge 是每个包的文件落点问题 —— 任何配方都不应再往
`/usr/sbin`、`/usr/lib64`、`/sbin` 里装文件。

---

## 7. Stage2 已知缺陷与 Extra 修复

Stage2 包集部署时暴露的问题，以及 `Extra/` 树的修复状态（2026-08-21）：

| 缺陷 | 修复位置 | 状态 |
| :--- | :--- | :--- |
| **ELF 扫描不生成 provides** | sage 源码 `e02aaaf` | ✅ 已修复 |
| **构建期不校验 `DT_NEEDED`** | sage 源码 `e02aaaf` | ✅ 已修复 |
| **仓库缺 device-mapper / icu** | `Extra/recipes/{device-mapper,icu}*/` | ✅ 已添加 |
| **xfsprogs 误链宿主库** | `Extra/recipes/xfsprogs/` (release 3) | ✅ 已修复 |
| **配方往 `/usr/sbin` 装** | `Extra/recipes/{util-linux,dosfstools,xfsprogs}/` | ✅ 已修复 |
| **grub 只有 BIOS 平台** | `Extra/recipes/grub/` (release 3, UEFI-only) | ✅ 已修复 |
| **仓库缺 tzdata / busybox / sudo** | `Extra/recipes/{tzcode,tzdata,busybox,sudo}/` | ✅ 已添加 |
| **`hostname` 双重提供** | `Extra/recipes/inetutils/` (release 3, `--disable-hostname`) | ✅ 已修复 |
| **缺 initramfs / 引导器触发器** | sage 源码 `945605c` + `Extra/recipes/{mkinitcpio,grub}/` | ✅ 已修复 |

**mkinitcpio 41.1 遗留问题**（配方已打补丁缓解）：

- **不读 `/etc/mkinitcpio.conf.d/`** —— `Extra/recipes/mkinitcpio/` 装了 `README`
  说明该目录不生效，避免用户误配。
- **`systemd` hook 的 nullglob 缺失** —— prepare 阶段打补丁 `shopt -s nullglob`。

`Extra/build-extra.sh` 按拓扑序构建 17 个包（7 个 Stage2 重建 + 10 个新增），
产物落 `Extra/repo/`。**用户自行运行该脚本**。

---

## 8. 写作约定

- 文档与注释用中文，代码标识符、命令、配置键用英文。
- 新增 markdown 会被 `tests/check-links.py` 校验链接与锚点。
- 提交信息用 Conventional Commits（`feat:` / `fix:` / `docs:` / `ci:` / `chore:`），
  **正文与标题一律用英文**。这是提交信息的唯一例外 —— 文档、注释仍用中文。
