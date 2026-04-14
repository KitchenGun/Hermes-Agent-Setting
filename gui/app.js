const bridgeMode = document.getElementById("bridge-mode");
const runtimeStatus = document.getElementById("runtime-status");
const modeValue = document.getElementById("mode-value");
const runningValue = document.getElementById("running-value");
const lastPromptValue = document.getElementById("last-prompt-value");
const responseBox = document.getElementById("response-box");
const eventList = document.getElementById("event-list");
const promptInput = document.getElementById("prompt-input");
const contextInput = document.getElementById("context-input");
const refreshButton = document.getElementById("refresh-button");
const startButton = document.getElementById("start-button");
const stopButton = document.getElementById("stop-button");
const sendButton = document.getElementById("send-button");

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function renderEvents(events) {
  eventList.innerHTML = "";
  const items = [...events].reverse();
  if (!items.length) {
    eventList.innerHTML = "<div class='event'><pre>아직 기록된 이벤트가 없습니다.</pre></div>";
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "event";
    card.innerHTML = `
      <header>
        <span class="event-kind">${item.kind}</span>
      </header>
      <pre>${JSON.stringify(item.payload, null, 2)}</pre>
    `;
    eventList.appendChild(card);
  }
}

function renderStatus(data) {
  const status = data.status || {};
  bridgeMode.textContent = data.mode || "unknown";
  runtimeStatus.textContent = status.running ? "running" : "idle";
  modeValue.textContent = status.mode || data.mode || "-";
  runningValue.textContent = String(Boolean(status.running));
  lastPromptValue.textContent = status.last_prompt || "-";
  renderEvents(data.events || []);
}

async function loadStatus() {
  try {
    const data = await request("/api/status");
    renderStatus(data);
  } catch (error) {
    responseBox.textContent = String(error);
  }
}

async function runAction(path, body) {
  const method = body ? "POST" : "POST";
  startButton.disabled = true;
  stopButton.disabled = true;
  sendButton.disabled = true;
  refreshButton.disabled = true;

  try {
    const data = await request(path, {
      method,
      body: body ? JSON.stringify(body) : undefined,
    });
    responseBox.textContent = JSON.stringify(data, null, 2);
    await loadStatus();
  } catch (error) {
    responseBox.textContent = String(error);
  } finally {
    startButton.disabled = false;
    stopButton.disabled = false;
    sendButton.disabled = false;
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", () => loadStatus());
startButton.addEventListener("click", () => runAction("/api/start"));
stopButton.addEventListener("click", () => runAction("/api/stop"));
sendButton.addEventListener("click", () => {
  runAction("/api/send", {
    prompt: promptInput.value,
    context: contextInput.value,
  });
});

loadStatus();
setInterval(loadStatus, 4000);
