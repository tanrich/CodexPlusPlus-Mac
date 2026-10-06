# CodexPlusPlus-Mac

A small macOS command-line adapter for Codex's official browser extension in API-key mode. Inspired by [Codex++](https://github.com/BigPizzaV3/CodexPlusPlus), with its original helper preserved and attributed. This is an independent project by **tanrich**, not an official Codex++ port or an OpenAI product.

中文说明如下。English instructions: [README.en.md](README.en.md).

在 macOS 上保留 API-key 登录，通过官方 Edge 扩展操作浏览器。本工具为本地服务添加一个明确开启的请求标识要求，用外置服务替换浏览器服务映射；它不会切换登录方式或修改 API Key。

## 当前支持范围

| 项目 | 支持情况 |
| --- | --- |
| 操作系统 | macOS，Apple Silicon / arm64 |
| Python | 3.9 或更新版本，仅使用标准库 |
| 已验证 CUA runtime | `0.0.16/20260915001755-492f19756c31`，Node `24.21.0` |
| 浏览器 | 官方 Edge 扩展路径已实测；Chrome helper 分支仅离线测试 |
| 版本变化 | 完整文件指纹不匹配就拒绝安装/启用，不自动信任新版本 |

不是 Codex++ 的完整功能移植。上游已列出 macOS 安装包；本项目专注于这一个可独立查看、启用和回滚的浏览器适配工具。

## 安装

先打开 Codex/ChatGPT App 和 Edge，并安装、启用官方浏览器扩展。保持现有 API-key 登录。

```sh
git clone https://github.com/tanrich/CodexPlusPlus-Mac.git
cd CodexPlusPlus-Mac
python3 manage-adapter.py check
python3 manage-adapter.py install
python3 ~/.codex/codexplusplus-mac/bin/manage-adapter.py alias
```

新开一个终端，运行：

```sh
manage-adapter enable
manage-adapter status
```

`install` 只生成默认关闭的外置副本和本地备份；`enable` 才修改浏览器服务映射并打开适配开关。脚本、校验文件、备份和服务安装在 `~/.codex/codexplusplus-mac/`，安装后不依赖下载的仓库目录。`alias` 在 `~/.zshrc` 添加 `manage-adapter`，保存修改前备份；若已有不同的 alias，拒绝覆盖。

如果已经使用另一份浏览器适配脚本，先用那份脚本恢复官方服务映射，并手动核对原来的 alias。本工具不覆盖已有适配，也不自动迁移旧安装。

找不到 App 或遇到多个插件缓存时，按错误提示明确指定路径：

```sh
python3 manage-adapter.py install \
  --app /Applications/ChatGPT.app \
  --descriptor "$HOME/.codex/plugins/cache/openai-bundled/unified-computer-use/<version>/.mcp.json"
```

上面的 `<version>` 要替换为 App 当前使用的插件版本，不能照抄。`--codex-home` 支持自定义 Codex 用户目录，也可使用 `CODEX_HOME` 环境变量。

## 重启后如何使用和验证

打开 Edge → 打开 Codex/ChatGPT App → 在终端执行 `manage-adapter enable` → 在 App **新建任务**，发送：

> 请重新初始化浏览器工具，通过 Edge 扩展读取当前标签页数量。

实际返回标签页数量才算连通。脚本不会重载已经运行的工具连接；App 重启或插件同步可能恢复缓存中的官方映射，因此需重新执行 `enable`。旧任务报 `Transport closed` 时，不能据此判断新连接也失败。

## 命令

| 命令 | 功能 |
| --- | --- |
| `python3 manage-adapter.py check` | 检查当前 App 是否匹配已验证 runtime，不修改配置 |
| `python3 manage-adapter.py install` | 安装默认关闭的外置服务、管理脚本和本地恢复备份 |
| `python3 ~/.codex/codexplusplus-mac/bin/manage-adapter.py alias` | 添加 zsh 快捷命令 |
| `manage-adapter status` | 中文状态说明；只检查本地文件和配置 |
| `manage-adapter enable` | 校验版本后启用；可以重复执行 |
| `manage-adapter restore` | 恢复官方浏览器服务映射，关闭本地适配 |
| `manage-adapter status --json` | 输出 JSON，适合其它脚本读取 |
| `manage-adapter` | 等同于 `status` |

示例状态：

```text
适配状态：已启用（配置已写入）
服务配置：本地适配服务
文件校验：通过
浏览器连接：未检测（此命令只检查本地配置）
验证方法：在 App 新建任务，让它通过 Edge 扩展读取当前标签页数量。
```

`restore` 用于停用适配、排查异常，或切回 ChatGPT 登录后使用官方实现。它不切换登录方式。API-key 模式下恢复官方映射后，原认证错误可能再次出现。

## 实测结果与限制

2026-10-06，在上述 arm64 runtime 的新官方 CUA 连接中，Edge 标签枚举、网页读取、点击和中文输入精确回显成功。开源版将原脚本改为可移植安装流程；这些浏览器结果来自同一 helper 与回调的本地前身，不能称为所有用户、版本和站点都已验证。开源版安装/启用/回滚另有合成环境测试。

- 本地请求头检查导航曾返回 `net::ERR_BLOCKED_BY_CLIENT`，没有取得头值；标签关闭曾报请求头准备错误，最终确认测试标签已不存在。完整稳定性仍未验证。
- 受控请求可能携带 `x-browser-agent: ChatGPT/<session-id>`。这是浏览器代理标识，不是登录 token；网站可能据此识别代理访问。扩展可能保留标识启用状态，`restore` 不清除该扩展状态。
- 不修改认证文件、App bundle、扩展本体、站点审批或代码信任根。用户拒绝/停止、扩展状态清理、App 升级和跨重启行为没有完整实测。版本更新需要单独检查和增加已验证 profile。

完整 App 服务只在用户本机读取并生成外置副本，本仓库不分发 App 服务代码或本地备份。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
node --experimental-vm-modules --test tests/helper.test.mjs
```

Python 测试使用临时目录和合成 runtime，不需要安装 Codex，也不访问登录凭据。Helper 测试需要 Node.js 22+。真实浏览器测试只能通过官方浏览器工具连接执行，`status` 不能代替它。

## 许可证和致谢

**AGPL-3.0-only**。自写管理代码：Copyright (C) 2026 tanrich。上游 helper：Copyright (C) 2026 BigPizzaV3，源自固定提交 [`f55bb646`](https://github.com/BigPizzaV3/CodexPlusPlus/commit/f55bb64663ba5024ab434017bb6212a0fd9f4bb3)。见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
