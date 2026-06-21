# 把 `claude -p` 当 API 用：拿 Claude 订阅跑出 API 的效果

> 不想为聊天接口再单独充 API 的钱？本机 `claude -p` 走的就是你已经付过的**订阅额度**——把它调成一个干净的**纯聊天接口**：扒光工具省额度、还能命中缓存，等于拿订阅跑出了 API 的效果（省钱·命中缓存）。

**作者 · 离**　｜　🔗 在线预览（GitHub Pages）：<https://sanqianzilanyue-commits.github.io/claude-p-save-tokens/>

> 用本机 `claude -p`（终端 Claude Code 本人，走你自己的 Claude 订阅）给网页当后端做**纯聊天**时，
> 它默认是个「完整 agent」——每次都把**一整套工具说明书**塞进输入，一句话也要吃 **约 2.7 万 token**。
> 纯聊天根本用不着工具。把工具扒光、把臃肿的系统提示换成精简人设，一句话输入能砍到 **一两百 token**；
> 再让稳定的前缀**命中缓存**（对，官方订阅也照样命中），省到尘埃里。
>
> 下面每个数字都是 macOS 上当场实测的，每段代码都能直接抄。

---

## 0. 先说人话：默认为什么那么贵

`claude -p` 不是个「聊天接口」，它是**整个 Claude Code 本人**——一个能读写文件、跑命令、搜代码的 agent。
所以你每喊它一句，它的输入里其实默默塞着：

- 一整套**工具说明书**（Read / Write / Bash / Grep…十几个工具的用法定义，十几 k token）；
- 一大张**默认系统提示**（怎么当 agent、怎么用工具、安全规矩……又是几 k）。

于是哪怕你只说一句「陪我聊聊天」，输入也是 **约 2.7 万 token**（实测）。纯聊天场景里，这些全是白烧的额度。

**核心思路一句话**：纯聊天就别让它当 agent——**把工具扒光、把系统提示整张换掉**，输入立刻瘦成一两百 token。

---

## 1. 需要什么设备

朴素到有点好笑：

- **一台装了 Claude Code 的电脑**（macOS / Linux / Windows 都行），并且**登录过你的 Claude 订阅**
  （命令行跑过一次 `claude`、登录完即可；Max 订阅额度多、最舒服）。
- 验明正身：终端敲 `which claude` 能打印出路径（macOS 上通常是 `/opt/homebrew/bin/claude`），就齐活了。
- **只给自己用**：到此为止，不需要任何别的东西。
- **想让外网/手机也能用**：再加两样——让这台机器**别睡**（`caffeinate` 或 `pmset` 禁睡），
  和一个**内网穿透**（Tailscale / cloudflared / 反向隧道）把它的端口送上公网。这两样是另一篇的事，本篇只管省钱。

> 不需要 API key、不需要充值。`claude -p` 走的是你机器上登录的**订阅**，烧的是订阅额度——所以「省 token」＝「省你每天的订阅额度」。

---

## 2. 前端怎么接入（最小后端 + 最小前端）

浏览器没法直接调本机命令，得有个**后端**起一个 `claude -p` 子进程、把结果转给网页。最小可跑版：

### 后端（FastAPI · 省钱配置已就位）

```python
# app.py  ——  pip install fastapi uvicorn ；跑：uvicorn app:app --port 8000
import json, os, subprocess
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()
CLAUDE_BIN = "/opt/homebrew/bin/claude"          # which claude

# ★ 省额度的命门：纯聊天就别让它当 agent
#   --tools none        把整套工具说明书剥光（十几 k token 直接没了）
#   --system-prompt     整张系统提示「换掉」（不是 append！append 会把臃肿默认提示留着）
SYS = "你是一个温柔耐心的陪伴者，说话口语、贴心、有来有回。绝不使用任何工具、不读写文件、不执行命令，只管说话。"

class Req(BaseModel):
    prompt: str = ""

@app.post("/chat")
def chat(inp: Req):
    env = dict(os.environ)
    # 子进程环境很干净，常找不到 node/claude，手动补 PATH、HOME
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    env["HOME"] = os.path.expanduser("~")

    def gen():
        proc = subprocess.Popen(
            [CLAUDE_BIN, "-p",
             "--tools", "none",                  # ★ 扒光工具
             "--system-prompt", SYS,             # ★ 换掉系统提示（精简人设）
             "--output-format", "stream-json",
             "--verbose",
             "--include-partial-messages",       # 少了这个就没有逐字增量
             inp.prompt],
            cwd=env["HOME"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        try:
            for line in proc.stdout:             # 逐行读 claude 吐的 JSONL
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                # 正文增量都包在 stream_event.event.content_block_delta.delta.text 里
                if j.get("type") == "stream_event":
                    d = (j.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        yield "data: " + json.dumps({"delta": d["text"]}, ensure_ascii=False) + "\n\n"
                # 结尾结账：把这轮 token / 缓存账单转给前端
                elif j.get("type") == "result":
                    u = j.get("usage") or {}
                    yield "data: " + json.dumps({"usage": {
                        "in": u.get("input_tokens"),
                        "cached": u.get("cache_read_input_tokens"),
                        "cacheWrite": u.get("cache_creation_input_tokens"),
                        "out": u.get("output_tokens"),
                    }}, ensure_ascii=False) + "\n\n"
        finally:
            try: proc.terminate()
            except Exception: pass
            yield "data: " + json.dumps({"done": True}) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no",   # 公网前面有 nginx 时，防它把 SSE 憋成一坨
        "Cache-Control": "no-cache",
    })
```

### 前端（原生 JS，边读边显）

```js
async function chat(prompt, onText) {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", text = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      let j; try { j = JSON.parse(line.slice(5).trim()); } catch { continue; }
      if (j.delta) { text += j.delta; onText(text); }          // 正文累加、实时刷新
      if (j.usage) console.log("这轮账单", j.usage);            // 看省了多少，见第 4 节
    }
  }
  return text;
}

// 用法
chat("陪我聊聊天", (t) => { box.textContent = t; });
```

> 想顺带把模型「开口前的真思维链」也流出来？那是另一篇专门讲的事，参见
> [claude-p-thinking-stream](https://sanqianzilanyue-commits.github.io/claude-p-thinking-stream/)。本篇专心省钱。

---

## 3. 怎么省 token（订阅额度）—— 核心一招

记住两个参数的**生死差别**：

| 参数 | 干了什么 | 一句话纯聊天输入 |
|---|---|---|
| 默认（什么都不加） | 完整 agent，带全套工具 + 默认系统提示 | **约 2.7 万 token** |
| `--append-system-prompt "…"` | 在**臃肿默认提示后面追加**——工具说明书**还在** | 还是两万多（白忙） |
| `--system-prompt "…"` + `--tools none` | 把系统提示**整张换成**精简人设、工具**扒光** | **一两百 token** |

最容易踩的坑：很多人想省钱时用了 `--append-system-prompt`——**那是「追加」，臃肿的默认提示和整套工具一个没少**，等于没省。
要省，必须用 `--system-prompt`（**替换**整张）**外加** `--tools none`（剥光工具）。

**实测**（macOS，2026-06）：

```bash
# 扒光工具 + 换掉系统提示，问一句话
claude -p --tools none --system-prompt "你只管说话，绝不用工具。" \
  --output-format json "说一个字：好" 2>/dev/null \
| python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print('input_tokens =', u['input_tokens'])"
# → input_tokens = 183
```

一句话从**约 2.7 万**砍到 **183**，差着**百来倍**。带上正经的人设（几百字），也就几百到一两 k token，依旧便宜得离谱。

> 安全副作用（白赚的）：`--tools none` 之后它**没有手**了——读不了文件、跑不了命令，
> 拿去给公网网页当聊天后端时，等于顺手把「被人借你的机器乱翻乱跑」这个风险也焊死了。

---

## 4. 怎么做命中缓存（对，官方订阅也能命中）

很多人以为「提示缓存」是 API 才有的、订阅版没有。**错。** `claude -p` 走订阅照样缓存，
省下来的 `cache_read` token 只按**约 1/10** 计费。亲手实测给你看：

```bash
# 造一段约 3000 字、逐字不变的系统提示，连打两次同样的前缀
SP=$(python3 -c "print('你是一个温柔耐心的陪伴者，说话口语、贴心、有来有回。'*120)")

# 第 1 次（建缓存）
claude -p --tools none --system-prompt "$SP" --output-format json "说：第一次" 2>/dev/null \
| python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print('creation=',u['cache_creation_input_tokens'],'read=',u['cache_read_input_tokens'])"
# → creation= 3887  read= 0          （把这段前缀写进了缓存）

# 第 2 次（同一前缀，应命中）
claude -p --tools none --system-prompt "$SP" --output-format json "说：第二次" 2>/dev/null \
| python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print('creation=',u['cache_creation_input_tokens'],'read=',u['cache_read_input_tokens'])"
# → creation= 137   read= 3750       （★命中！3750 token 从缓存白拿，只收约 1/10）
```

第二次那 **`read=3750`** 就是省下的真金白银——它没重新「读」一遍三千字的系统提示，直接从缓存里捞。

### 命中缓存的三条铁律

1. **稳定前缀逐字不变。** 缓存认的是输入**前缀**的逐字哈希——系统提示哪怕改动一个字、一个空格，
   缓存就从那个字开始整段作废、重新计费。所以把**不变的东西**（人设、世界观、规则）放在最前面、一个字别动。
2. **会变的东西全挪到末尾。** 时间、当前心情、动态上下文这类每次都不一样的，**绝不能掺进稳定前缀**——
   要么放进**用户消息**里，要么放在系统提示**最后**。一旦它们混进前缀，每次哈希都变、缓存永远命不中。
   （如果你用 `--system-prompt` 之外还想避开 Claude Code 自己注入的动态段落，可加 `--exclude-dynamic-system-prompt-sections`，让前缀更干净更稳。）
3. **前缀要够长 + 趁热打。** 提示缓存有**最低门槛**（约 1024 token 才开始缓存）——太短的前缀**不会**被缓存
   （这也是第 3 节那个 183 token 的例子 `cache_read` 是 0 的原因：太短，够不着门槛）。
   而且缓存有 **约 5 分钟 TTL**：5 分钟内有来有回，缓存一直热着、连续命中；隔太久它过期，下次得重新建（再花一次 `creation`）。

> 一句话：**把不变的铺在前头、够长、别动；把会变的甩到后头。** 缓存就一直命中，额度就一直省。

---

## 5. 一步到位 · 傻子版

不想看原理，照抄就行：

**① 装依赖、存后端**

```bash
pip install fastapi uvicorn
# 把第 2 节那个 app.py 存下来（已含 --tools none + --system-prompt 省钱配置）
```

**② 起后端**

```bash
uvicorn app:app --port 8000
```

**③ 一行命令验证省钱 + 流式都正常**

```bash
curl -sN -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"陪我聊聊天，说三句"}'
# 一股一股冒字 = 流式正常；最后那条 {"usage":{"in": …}} 里 in 是一两百 = 省钱配置生效
```

**④ 想连命中缓存一起占满**：把 `app.py` 里的 `SYS` 换成一段**够长（≥约 1024 token）、固定不变**的人设/世界观，
别把时间、心情这种每次会变的写进去——它就会从第二轮起一直命中缓存。

不接网页、就想在终端省着聊，一行就够：

```bash
claude -p --tools none \
  --system-prompt "你是温柔的陪伴者，只管说话，绝不使用任何工具、不读写文件、不执行命令。" \
  "陪我聊聊天"
```

---

## 一句话总结

纯聊天别让 `claude -p` 当 agent：**`--tools none` 扒光工具 + `--system-prompt`（替换、别 append）换成精简人设**，
一句话输入从约 2.7 万 token 砍到一两百；再把**不变的长前缀**铺在最前头吃**提示缓存**（订阅也命中，省约 9 成），
**会变的甩到末尾**。省额度，就省到这个份上。

---

*作者 · 离　|　写于 2026 · 实测于 macOS + Claude 订阅 + FastAPI*
*自由取用 · 照着搓就好 🐾*
