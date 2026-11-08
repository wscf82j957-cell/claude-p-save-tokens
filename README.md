# 本机对话

把这台 Mac 上已经登录的 Claude Code，收成一个只给你自己用的文字页面。手机可以在同一个 Wi-Fi 里打开它。

这不是官方网站，也不提供公开接口。仓库里原来的教程只讲了做法，不能直接运行，而且示例会把整段话放进命令参数、工作目录设成主目录、接口没有口令。现在的程序按官方客户端文档重新写过，没有照抄那些参数。

## 先满足这些条件

- 一台 macOS 13 或更新的 Mac，Intel 或 Apple Silicon 都可以。它要一直开着、醒着，并且能上网。程序不会替你改睡眠、不会开机自启、不会新建系统账户。
- 这台 Mac 上已经装好 Claude Code，并且你自己用订阅登录过。包含 Claude Code 的是 Pro、Max、Ultra、Team 或 Enterprise。本程序不登录、不读取、不复制登录令牌，也不购买任何东西。
- Python 3.11 或更新。
- 客户端版本至少 2.1.259。启动时会看 `claude --version` 和 `claude --help`，缺参数就停。查找顺序是：`CLAUDE_BIN`、当前 `PATH`、`~/.local/bin/claude`（官方安装器的位置）、`/opt/homebrew/bin/claude`（Apple Silicon 上常见的 Homebrew）、`/usr/local/bin/claude`（Intel 上常见的 Homebrew）。没有写死只用 Homebrew。

这里的自动测试跑在 Linux 上，用的是假 claude，没有调用真实模型。macOS 实机没有跑过。

## 先在电脑上跑通

在那台要长期开着的 Mac 上，进入这个目录：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

安装只会建立本目录的虚拟环境，并在 `runtime/auth.token` 写一个权限为 600 的口令。这个文件被 git 忽略。终端是交互屏幕时，启动会把口令打出来一次。不要把它提交，也不要发到别处。

然后在这台 Mac 自己的浏览器打开：

```text
http://127.0.0.1:8765/
```

把口令填进页面。默认只监听 127.0.0.1，手机还打不开。

停止：

```bash
bash scripts/stop.sh
```

电脑重启后不会自己再起来。要继续用，就再执行一次 `bash scripts/start.sh`。

## 再让手机在同一个 Wi-Fi 里打开

电脑和手机连到同一个 Wi-Fi。先停掉服务，再显式打开同一网络模式：

```bash
bash scripts/stop.sh
bash scripts/start.sh --lan
```

终端会打印类似 `http://192.168.x.x:8765/` 的地址。用手机浏览器打开那个地址，填同一个口令。

这一步仍然要求：

- 每次对话都要带口令。
- 浏览器的 Origin 必须是这台服务自己的地址。
- 页面要先拿到 CSRF 令牌，请求里同时带上请求头和 Cookie。

`--lan` 会监听 `0.0.0.0`，所以同一个网络里的人只要拿到口令就能用你的订阅。不要把口令告诉别人。地址变了就先停止再启动。需要指定地址时，可以设置 `LAN_ORIGINS=http://192.168.x.x:8765`。

## 出门以后

人离开这个 Wi-Fi 之后，`127.0.0.1` 和 `--lan` 打印的地址都不能用。外出要另外准备一条你自己信任的安全路径。本程序不做内网穿透、VPN 或公网部署，也不会把“只监听本机”说成手机在外面也能用。

电脑睡着、关机或断网时，页面都会停。程序故意不阻止睡眠。

## 它怎样调用 claude

每一次对话都新开一个进程，用户的话只放在标准输入的一条 JSON 里，不放进命令参数，所以也不会出现在进程列表里。命令行里的说明文字是固定的。用到的官方参数是：

- `--input-format stream-json` 和 `--output-format stream-json`
- `--tools ""` 关掉内置工具，并加上 `--disallowed-tools *` 和 `mcp__*`
- `--restricted` 和 `--safe-mode`：不读 bare 模式，订阅登录仍可用；用户目录里的钩子、技能、插件和 MCP 不作为配置来源
- `--strict-mcp-config` 配一份空的 `{"mcpServers":{}}`
- `--permission-mode dontAsk`、`--permission-prompts none`、`--max-turns 1`
- `--no-session-persistence`、`--disable-slash-commands`
- 固定的 `--system-prompt`

没有使用 `--bare`，也没有设置 `CLAUDE_CODE_SIMPLE`。传给子进程的环境只留 `HOME`、用户名、UTF-8 语言、临时目录和一条重新拼出来的 `PATH`。`ANTHROPIC_API_KEY`、云厂商变量和 OAuth 变量都不会传进去，避免 `-p` 改用 API 计费。

启动后要先看到官方的 `system/init`：`tools` 和 `mcp_servers` 必须是空数组，插件和技能也必须是空的。`apiKeySource` 如果出现，必须是 `none`（官方对“没有 API 密钥、改走订阅或其他登录”的取值）。确认失败就停止，不把后面的文字发给页面。失败和超时时用 `SIGKILL` 结束进程。官方文档写明 `SIGTERM` 仍可能跑 `SessionEnd` 钩子。

这些参数来自 Claude Code 的 CLI、headless 和 Agent SDK 文档。本仓库的测试没有拿真的客户端跑过，所以第一次在 Mac 上如果启动确认失败，先看页面上的错误代码，不要把服务暴露到 Wi-Fi 之外。

多轮对话留在手机页面上。每次发送都会把旧对话按“用户 / 助手”的顺序放进这一次的用户消息，系统提示不变。服务不把这段话写进会话文件，日志里也不记提示词和口令。同一时间只处理一条，默认 120 秒超时。单条最多 8000 字，整段历史最多 32000 字。

组织管理员下发的托管设置仍可能在 `--restricted` 之外生效。真出现工具或钩子时，这一轮会停。

## 测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

测试使用 `tests/mock_claude.py`，不登录，也不调用模型。覆盖参数注入、认证和来源、环境清理、超时后进程消失、工具名单、错误时不回传上游原文。`tests/test_scripts.py` 只在这台 Linux 上用假客户端试过启动和停止。
