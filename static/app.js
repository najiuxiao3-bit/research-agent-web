/* ===== 资料查询总结助手 · 工作台逻辑（SSE 流式） ===== */
(function () {
  "use strict";

  // ----- DOM 缓存 -----
  const historyList = document.getElementById("history-list");
  const newBtn = document.getElementById("new-btn");
  const messagesInner = document.getElementById("messages-inner");
  const emptyState = document.getElementById("empty-state");
  const queryEl = document.getElementById("query");
  const submitBtn = document.getElementById("submit-btn");
  const sessionTitle = document.getElementById("session-title");
  const modelBadge = document.getElementById("model-badge");
  const statBadge = document.getElementById("stat-badge");

  // ----- 视图切换与浏览器自动化（独立功能，不参与调研链路） -----
  const sidebar = document.getElementById("sidebar");
  const viewTabs = document.getElementById("view-tabs");
  const viewResearch = document.getElementById("view-research");
  const viewBrowser = document.getElementById("view-browser");
  const browserKeyword = document.getElementById("browser-keyword");
  const browserRun = document.getElementById("browser-run");
  const browserResult = document.getElementById("browser-result");
  let browserBusy = false;

  // ----- 状态 -----
  let currentId = null;
  let busy = false;
  let sessions = [];

  // ----- 工具函数 -----
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatTime(iso) {
    if (!iso) return "";
    return String(iso).replace("T", " ").slice(0, 16);
  }

  function renderMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/^### (.*)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.*)$/gm, "<h3>$1</h3>");
    html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );
    html = html.replace(/^- (.*)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function sourcesHtml(sources) {
    if (!sources || sources.length === 0) return "";
    const items = sources
      .map(
        (s) =>
          `<div class="source-card">
            <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}<span class="link-arrow">↗</span></a>
            ${s.snippet ? `<div class="snippet">${escapeHtml(s.snippet)}</div>` : ""}
          </div>`
      )
      .join("");
    return `<div class="sources">
      <p class="sources-title">参考来源（${sources.length}）</p>
      <div class="source-grid">${items}</div>
    </div>`;
  }

  // ----- 顶栏统计 -----
  function refreshBadges() {
    const totalSources = sessions.reduce((sum, r) => sum + (r.n_sources || 0), 0);
    const model = (sessions[0] && sessions[0].model) || "Research Agent";
    modelBadge.textContent = model;
    statBadge.textContent = `${sessions.length} 个会话 · ${totalSources} 条来源`;
  }

  // ----- 消息流 -----
  function showEmpty(show) {
    emptyState.style.display = show ? "block" : "none";
    messagesInner.style.display = show ? "none" : "flex";
  }

  function appendUser(text) {
    const div = document.createElement("div");
    div.className = "msg-user";
    div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    messagesInner.appendChild(div);
    scrollBottom();
  }

  // 新建流式 Agent 卡片骨架，返回内部节点引用
  function appendStreamCard() {
    const root = document.createElement("div");
    root.className = "msg-agent";
    root.innerHTML = `
      <div class="avatar" aria-hidden="true">🤖</div>
      <div class="card">
        <div class="card-head">
          <span class="name">Research Agent</span>
          <span class="stream-status">正在连接…</span>
        </div>
        <div class="answer-body"></div>
        <div class="sources"></div>
      </div>`;
    messagesInner.appendChild(root);
    scrollBottom();
    return {
      root,
      status: root.querySelector(".stream-status"),
      body: root.querySelector(".answer-body"),
      sources: root.querySelector(".sources"),
    };
  }

  function renderStreaming(body, text) {
    body.innerHTML = escapeHtml(text).replace(/\n/g, "<br>") + '<span class="cursor"></span>';
    scrollBottom();
  }

  function showStreamError(card, message) {
    card.root.classList.add("msg-error");
    card.status.textContent = "";
    card.body.innerHTML = escapeHtml(message);
    scrollBottom();
  }

  function agentCardHtml(data) {
    const time = formatTime(data.created_at);
    return `
      <div class="avatar" aria-hidden="true">🤖</div>
      <div class="card">
        <div class="card-head">
          <span class="name">Research Agent</span>
          ${time ? `<span>· ${time}</span>` : ""}
          ${data.turns ? `<span>· ${data.turns} 轮检索</span>` : ""}
        </div>
        <div class="answer-body">${renderMarkdown(data.answer || "")}</div>
        ${sourcesHtml(data.sources || [])}
      </div>`;
  }

  function renderSession(detail) {
    messagesInner.innerHTML = "";
    showEmpty(false);
    appendUser(detail.query);
    const wrap = document.createElement("div");
    wrap.className = "msg-agent";
    messagesInner.appendChild(wrap);
    wrap.innerHTML = agentCardHtml(detail);
    sessionTitle.textContent = detail.query;
    scrollBottom();
  }

  function scrollBottom() {
    const box = document.querySelector(".messages");
    if (box) box.scrollTop = box.scrollHeight;
  }

  // ----- SSE 读取 -----
  async function readSSE(resp, handlers) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        let event = "message";
        let data = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          const payload = JSON.parse(data);
          if (handlers[event]) handlers[event](payload);
        } catch (_) {
          /* 忽略解析坏帧 */
        }
      }
    }
  }

  // ----- 历史会话 -----
  async function loadHistory() {
    try {
      const resp = await fetch("/api/history");
      const data = await resp.json();
      sessions = data.records || [];
      refreshBadges();

      if (sessions.length === 0) {
        historyList.innerHTML = '<li class="empty" style="padding:8px 12px;">暂无历史会话</li>';
        return;
      }
      historyList.innerHTML = sessions
        .map(
          (r) => `<li data-id="${r.id}" class="${r.id === currentId ? "active" : ""}">
            <div class="q">${escapeHtml(r.query)}</div>
            <div class="meta">
              <span>${formatTime(r.created_at)}</span>
              <span class="tag">${r.n_sources} 条来源</span>
            </div>
          </li>`
        )
        .join("");
      historyList.querySelectorAll("li[data-id]").forEach((li) => {
        li.addEventListener("click", () => selectSession(Number(li.dataset.id)));
      });
    } catch (err) {
      historyList.innerHTML = '<li class="empty" style="padding:8px 12px;">历史加载失败</li>';
    }
  }

  async function selectSession(id) {
    if (busy || id === currentId) return;
    try {
      const resp = await fetch(`/api/history/${id}`);
      if (!resp.ok) {
        appendError("记录加载失败");
        return;
      }
      const rec = await resp.json();
      currentId = id;
      renderSession(rec);
      loadHistory();
    } catch (err) {
      appendError(`网络错误：${err.message}`);
    }
  }

  function appendError(message) {
    const div = document.createElement("div");
    div.className = "msg-agent msg-error";
    div.innerHTML = `<div class="avatar" aria-hidden="true">⚠️</div>
      <div class="card">${escapeHtml(message)}</div>`;
    messagesInner.appendChild(div);
    scrollBottom();
  }

  function newSession() {
    if (busy) return;
    currentId = null;
    messagesInner.innerHTML = "";
    showEmpty(true);
    sessionTitle.textContent = "新的调研";
    loadHistory();
    queryEl.focus();
  }

  // ----- 提交提问（流式） -----
  async function submit(query) {
    query = query.trim();
    if (!query || busy) return;

    busy = true;
    submitBtn.disabled = true;
    showEmpty(false);
    appendUser(query);
    const card = appendStreamCard();
    let fullText = "";

    try {
      const resp = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!resp.ok) {
        let detail = "请求失败";
        try {
          const errBody = await resp.json();
          detail = errBody.detail || detail;
        } catch (_) {}
        showStreamError(card, detail);
        return;
      }

      await readSSE(resp, {
        status: (d) => {
          card.status.textContent = d.text;
        },
        sources: (d) => {
          card.sources.innerHTML = sourcesHtml(d.sources || []);
        },
        delta: (d) => {
          fullText += d.text || "";
          renderStreaming(card.body, fullText);
        },
        done: (d) => {
          card.body.innerHTML = renderMarkdown(fullText);
          const time = formatTime(d.created_at);
          card.status.innerHTML =
            (time ? `<span>· ${time}</span>` : "") +
            (d.turns ? `<span>· ${d.turns} 轮检索</span>` : "");
          currentId = d.id;
          sessionTitle.textContent = query;
          loadHistory();
        },
        error: (d) => showStreamError(card, d.detail || "模型调用失败"),
      });
    } catch (err) {
      showStreamError(card, `网络错误：${err.message}`);
    } finally {
      busy = false;
      submitBtn.disabled = false;
      queryEl.value = "";
      autoGrow();
    }
  }

  // ----- 输入框自适应高度 -----
  function autoGrow() {
    queryEl.style.height = "auto";
    queryEl.style.height = Math.min(queryEl.scrollHeight, 160) + "px";
  }

  // ----- 事件绑定 -----
  submitBtn.addEventListener("click", () => submit(queryEl.value));

  queryEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(queryEl.value);
    }
  });
  queryEl.addEventListener("input", autoGrow);

  newBtn.addEventListener("click", newSession);

  emptyState.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    queryEl.value = chip.dataset.question || "";
    autoGrow();
    submit(queryEl.value);
  });

  // ----- 视图切换 -----
  function switchView(view) {
    const isResearch = view === "research";
    viewResearch.classList.toggle("hidden", !isResearch);
    viewBrowser.classList.toggle("hidden", isResearch);
    sidebar.classList.toggle("hidden", !isResearch);
    viewTabs.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.view === view);
    });
    if (isResearch) {
      modelBadge.textContent = "Research Agent";
    } else {
      modelBadge.textContent = "Browser Tool";
      sessionTitle.textContent = "浏览器自动化";
      browserKeyword.focus();
    }
  }

  viewTabs.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) switchView(tab.dataset.view);
  });

  // ----- 浏览器自动化：直接调独立后端接口，不走 Agent、不写调研历史 -----
  function browserRunHandler() {
    const keyword = browserKeyword.value.trim();
    if (!keyword || browserBusy) return;

    browserBusy = true;
    browserRun.disabled = true;
    browserResult.innerHTML =
      '<div class="browser-status"><div class="spinner"></div> 正在打开百度并进入第一条结果，请稍候…</div>';

    fetch("/api/browser/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword }),
    })
      .then(async (resp) => {
        if (!resp.ok) {
          let detail = "浏览器操作失败";
          try {
            const err = await resp.json();
            detail = err.detail || detail;
          } catch (_) {}
          throw new Error(detail);
        }
        return resp.json();
      })
      .then((data) => {
        const title = escapeHtml(data.title || "无标题");
        const url = escapeHtml(data.url || "");
        const content = escapeHtml(data.content || "");
        const msg = data.message
          ? `<div class="browser-note">${escapeHtml(data.message)}</div>`
          : "";
        browserResult.innerHTML = `
          <div class="browser-card">
            <div class="browser-card-head">
              <span class="name">抓取结果</span>
              <span class="time">关键词：${escapeHtml(keyword)}</span>
            </div>
            <a class="browser-title" href="${url}" target="_blank" rel="noopener">${title}<span class="link-arrow">↗</span></a>
            ${url ? `<div class="browser-url">${url}</div>` : ""}
            <div class="browser-body">${content || '<span class="muted">未抽取到正文。</span>'}</div>
            ${msg}
          </div>`;
      })
      .catch((err) => {
        browserResult.innerHTML = `
          <div class="browser-card browser-error">
            <div class="browser-card-head"><span>⚠️ 运行失败</span></div>
            <div>${escapeHtml(err.message)}</div>
          </div>`;
      })
      .finally(() => {
        browserBusy = false;
        browserRun.disabled = false;
      });
  }

  browserRun.addEventListener("click", browserRunHandler);
  browserKeyword.addEventListener("keydown", (e) => {
    if (e.key === "Enter") browserRunHandler();
  });

  // ----- 初始化 -----
  loadHistory();
})();
