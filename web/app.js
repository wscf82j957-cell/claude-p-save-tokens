const tokenInput = document.querySelector("#token");
const connectButton = document.querySelector("#connect");
const statusLine = document.querySelector("#status");
const log = document.querySelector("#log");
const form = document.querySelector("#composer");
const text = document.querySelector("#text");
const clearButton = document.querySelector("#clear");

const messages = [];
let csrf = "";
let sending = false;

tokenInput.value = sessionStorage.getItem("localChatToken") || "";

function setStatus(message) {
  statusLine.textContent = message;
}

function render() {
  log.replaceChildren();
  messages.forEach((message) => {
    const block = document.createElement("p");
    block.className = "bubble " + (message.role === "user" ? "user" : "assistant");
    const who = message.role === "user" ? "我" : "助手";
    block.textContent = who + "：" + message.content;
    log.appendChild(block);
  });
  if (messages.usage) {
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = "本轮计数 " + JSON.stringify(messages.usage);
    log.appendChild(meta);
  }
}

async function connect() {
  const token = tokenInput.value.trim();
  if (!token) {
    setStatus("先填写口令。");
    return false;
  }
  const response = await fetch("/api/session", {
    headers: { Authorization: "Bearer " + token },
  });
  if (!response.ok) {
    csrf = "";
    setStatus("口令不对，或这个地址不在允许的来源里。");
    return false;
  }
  const data = await response.json();
  csrf = data.csrf || "";
  sessionStorage.setItem("localChatToken", token);
  setStatus("已连上这台电脑。对话轮次只留在这个页面里。");
  return true;
}

function appendAssistant() {
  messages.push({ role: "assistant", content: "" });
  return messages[messages.length - 1];
}

async function readStream(response, target) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const step = await reader.read();
    if (step.done) break;
    buffer += decoder.decode(step.value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const line = block.split("\n").find((item) => item.startsWith("data:"));
      if (line) {
        let payload;
        try {
          payload = JSON.parse(line.slice(5).trim());
        } catch (err) {
          payload = null;
        }
        if (payload && payload.delta) {
          target.content += payload.delta;
          render();
        }
        if (payload && payload.usage) {
          messages.usage = payload.usage;
          render();
        }
        if (payload && payload.error) {
          target.content = payload.message || "没有完成。";
          render();
        }
      }
      split = buffer.indexOf("\n\n");
    }
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (sending) return;
  const content = text.value.trim();
  if (!content) return;
  if (!csrf && !(await connect())) return;
  messages.push({ role: "user", content });
  text.value = "";
  render();
  sending = true;
  setStatus("正在等这台电脑上的 Claude Code。");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + tokenInput.value.trim(),
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
      },
      body: JSON.stringify({
        messages: messages.filter((item) => item.role === "user" || item.role === "assistant").map((item) => ({
          role: item.role,
          content: item.content,
        })),
      }),
    });
    if (response.status === 401 || response.status === 403) {
      csrf = "";
      messages.pop();
      render();
      setStatus("认证失败。请重新连接。");
      return;
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("text/event-stream")) {
      const data = await response.json().catch(() => ({}));
      appendAssistant().content = data.message || "没有完成。";
      render();
      setStatus("这一轮没有完成。");
      return;
    }
    const target = appendAssistant();
    await readStream(response, target);
    if (!target.content) target.content = "没有收到文字。";
    render();
    setStatus("可以继续说。历史在这个页面上，服务不保存。");
  } catch (err) {
    appendAssistant().content = "网络中断了。请确认电脑还开着，并且手机和电脑仍在同一个网络。";
    render();
    setStatus("连接中断。");
  } finally {
    sending = false;
  }
});

connectButton.addEventListener("click", () => {
  connect();
});

clearButton.addEventListener("click", () => {
  messages.splice(0, messages.length);
  delete messages.usage;
  render();
});
