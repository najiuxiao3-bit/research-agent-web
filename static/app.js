/* ===== 资料查询总结助手 · 前端逻辑 ===== */
(function () {
  "use strict";

  // ----- DOM 缓存 -----
  const form = document.getElementById("ask-form");
  const queryEl = document.getElementById("query");
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const answerEl = document.getElementById("answer");
  const sourcesEl = document.getElementById("sources");
  const historyList = document.getElementById("history-list");
  const submitBtn = document.getElementById("submit-btn");

  // ----- 工具函数 -----
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 轻量 Markdown：标题、加粗、链接、列表、换行
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

  function renderSources(sources) {
    if (!sources || sources.length === 0) {
      sourcesEl.innerHTML =
        '<h2>参考来源</h2><p class="empty">本次未取到可靠来源。</p>';
      return;
    }
    const items = sources
      .map(
        (s) =>
          `<div class="source-card">
            <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a>
            ${s.snippet ? `<div class="snippet">${escapeHtml(s.snippet)}</div>` : ""}
          </div>`
      )
      .join("");
    sourcesEl.innerHTML = `<h2>参考来源（${sources.length}）</h2><div class="source-grid">${items}</div>`;
  }

  function showResult(data) {
    resultEl.hidden = false;
    answerEl.innerHTML = renderMarkdown(data.answer || "");
    renderSources(data.sources || []);
  }

  function setStatus(message, isError) {
    statusEl.textContent = message || "";
    statusEl.className = isError ? "error" : "";
  }

  function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.innerHTML = loading
      ? '<span class="spinner"></span> 检索中…'
      : "开始调研";
  }

  // ----- 历史记录 -----
  function markActive(id) {
    historyList.querySelectorAll("li[data-id]").forEach((li) => {
      li.classList.toggle("active", Number(li.dataset.id) === id);
    });
  }

  async function loadHistory() {
    try {
      const resp = await fetch("/api/history");
      const data = await resp.json();
      const records = data.records || [];
      if (records.length === 0) {
        historyList.innerHTML = '<li class="empty">暂无历史记录</li>';
        return;
      }
      historyList.innerHTML = records
        .map(
          (r) => `<li data-id="${r.id}">
            <div class="q">${escapeHtml(r.query)}</div>
            <div class="meta">
              <span>${escapeHtml(r.created_at)}</span>
              <span class="tag">${r.n_sources} 条来源</span>
            </div>
          </li>`
        )
        .join("");
      historyList.querySelectorAll("li[data-id]").forEach((li) => {
        li.addEventListener("click", () => loadDetail(Number(li.dataset.id)));
      });
    } catch (err) {
      historyList.innerHTML = '<li class="empty">历史加载失败</li>';
    }
  }

  async function loadDetail(id) {
    setStatus("");
    try {
      const resp = await fetch(`/api/history/${id}`);
      if (!resp.ok) {
        setStatus("记录加载失败", true);
        return;
      }
      const rec = await resp.json();
      markActive(id);
      showResult(rec);
    } catch (err) {
      setStatus(`网络错误：${err.message}`, true);
    }
  }

  // ----- 提交提问 -----
  async function handleSubmit(e) {
    e.preventDefault();
    const query = queryEl.value.trim();
    if (!query) return;

    setLoading(true);
    setStatus("检索与提炼中，可能需要十几秒…");
    resultEl.hidden = true;

    try {
      const resp = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setStatus(data.detail || "请求失败", true);
        return;
      }
      setStatus("");
      markActive(data.id);
      showResult(data);
      loadHistory();
    } catch (err) {
      setStatus(`网络错误：${err.message}`, true);
    } finally {
      setLoading(false);
    }
  }

  form.addEventListener("submit", handleSubmit);
  loadHistory();
})();
