# sclinux 包拆分规则与 Channel 机制

## 一、包拆分规则

- **纯库包**：使用 `name` 和 `name-dev`，不额外拆分 `name-libs`。例如 `lmdb`、`mpdecimal`。
- **库与独立程序并存**：拆分为 `name`、`name-libs` 和 `name-dev`。独立 CLI、守护进程等放在主包中。
- **构建工具**：按工具功能保持完整，不机械拆分运行库。例如 `libtool` 合并运行库、headers、macros、pkg-config 文件和 CLI。
- **大型项目**：按功能拆分，而不是按文件类型机械拆分。systemd 拆分为：
  - `systemd-libs`
  - `systemd-udev`
  - `systemd`
  - `systemd-networkd`
  - `systemd-resolved`
  - `systemd-timesyncd`
  - `systemd-dev`
- **虚拟能力**：具体实现包同时提供自身名称和稳定 capability。例如：
  - `systemd` 提供 `virtual/init`
  - `systemd-udev` 提供 `virtual/udev`
  - `linux-zen` 提供 `linux`、`virtual/linux` 和 `virtual/kernel`
  - `linux-zen-headers` 提供 `linux-headers` 和 `virtual/linux-headers`
- **Meta 包**：只聚合依赖，不包含实际文件。

## 二、Channel 机制

一个 Channel 通常由配置中的名称和 URL 标识，目录结构如下：

```text
channel/
├── index.toml
└── *.pkg.tar.zst
```

### `index.toml`

索引记录包的：

- 名称、版本和 release
- 架构与描述
- 运行时依赖
- 构建/能力提供信息
- channel 归属

Sage 先同步或读取 Channel 的 `index.toml`，再根据依赖和 capability 选择包，最后从 Channel 目录或本地缓存中取得对应的 `.pkg.tar.zst` 归档并安装。

### Stage2 Channel

Stage2 构建脚本使用配置的 recipes 和 `BUILD_ORDER`：

1. 按依赖顺序构建包；
2. 将生成的 `.pkg.tar.zst` 复制到 `repo/`；
3. 使用 Sage 生成 `repo/index.toml`；
4. 将 `repo/` 作为本地 Channel 使用。

因此，包归档和 `index.toml` 必须来自同一次构建，不能混用旧索引或旧包。