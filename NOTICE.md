# 第三方组件许可说明

ShenChen Linux 仓库自身的内容采用根目录 [LICENSE](LICENSE) 中的 BSD
3-Clause License。由本仓库配方构建或随系统分发的上游组件，仍遵循各自的
许可证。

## Sage

- **组件：** Sage package manager
- **上游仓库：** <https://github.com/antinomie1/sage>
- **许可证：** BSD 2-Clause License
- **使用位置：** `Stage1/recipes/sage/recipe.toml` 构建上游 Sage；本仓库的
  `scripts/shc` 只是 ShenChen Linux 自有的命令前端，遵循根目录的 BSD
  3-Clause License。

Sage 的许可证全文如下：

```text
BSD 2-Clause License

Copyright (c) 2026, Sage Contributors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
