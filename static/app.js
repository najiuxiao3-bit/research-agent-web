/* ===== 资料查询总结助手 · 工作台逻辑 ===== */
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
            <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a>
            ${s.snippet ? `<div class="snippet">${escapeHtml(s.snippet)}</div>` : ""}
          </div>`
      )
      .join("");
    return `<div class="sources">
      <p class="sources-title">参考来源（${sources.length}）</p>
      <div class="source-grid">${items}</div>
    </div>`;
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

  function appendAgentLoading() {
    const div = document.createElement("div");
    div.className = "msg-agent";
    div.innerHTML = `<div class="avatar" aria-hidden="true">🤖</div>
      <div class="card">
        <div class="loading-line"><span class="spinner"></span> 正在联网检索与提炼，请稍候…</div>
      </div>`;
    messagesInner.appendChild(div);
    scrollBottom();
    return div;
  }

  function replaceAgent(placeholder, html) {
    placeholder.className = "msg-agent";
    placeholder.innerHTML = html;
    scrollBottom();
  }

  function appendError(message) {
    const div = document.createElement("div");
    div.className = "msg-agent msg-error";
    div.innerHTML = `<div class="avatar" aria-hidden="true">⚠️</div>
      <div class="card">${escapeHtml(message)}</div>`;
    messagesInner.appendChild(div);
    scrollBottom();
  }

  function renderSession(detail) {
    messagesInner.innerHTML = "";
    showEmpty(false);
    appendUser(detail.query);
    const wrap = document.createElement("div");
    wrap.className = "msg-agent";
    messagesInner.appendChild(wrap);
    replaceAgent(wrap, agentCardHtml(detail));
    sessionTitle.textContent = detail.query;
  }

  function scrollBottom() {
    const box = document.querySelector(".messages");
    if (box) box.scrollTop = box.scrollHeight;
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

  function newSession() {
    if (busy) return;
    currentId = null;
    messagesInner.innerHTML = "";
    showEmpty(true);
    sessionTitle.textContent = "新的调研";
    loadHistory();
    queryEl.focus();
  }

  // ----- 提交提问 -----
  async function submit(query) {
    query = query.trim();
    if (!query || busy) return;

    busy = true;
    submitBtn.disabled = true;
    showEmpty(false);
    appendUser(query);
    const placeholder = appendAgentLoading();

    try {
      const resp = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        replaceAgent(placeholder, `<div class="avatar" aria-hidden="true">⚠️</div>
          <div class="card msg-error">${escapeHtml(data.detail || "请求失败")}</div>`);
        return;
      }
      currentId = data.id;
      sessionTitle.textContent = query;
      replaceAgent(placeholder, agentCardHtml(data));
      loadHistory();
    } catch (err) {
      replaceAgent(placeholder, `<div class="avatar" aria-hidden="true">⚠️</div>
        <div class="card msg-error">网络错误：${escapeHtml(err.message)}</div>`);
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

  // ----- 初始化 -----
  loadHistory();
})();
