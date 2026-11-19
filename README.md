# 资料查询总结助手（Research Agent · Web 版）

一个从 0 到 1 的最小可交互 Agent 工作台，含两个**互不融合**的功能：

- **资料调研**：浏览器输入问题 → 后端联网检索 → 提炼带来源的中文摘要 → 对话与摘要存入 SQLite。
- **浏览器自动化**：输入关键词 → 自动打开百度结果页并进入第一条 → 提炼要点摘要、截取页面，回传前端卡片；**流程不由模型决策、不写调研库**，由独立接口与独立前端视图承载。

代码按「配置 / 提示词 / 工具 / LangGraph 状态图 / 存储 / Web 接口 / 前端」分层，便于照着改和扩展。调研 Agent 基于 **LangChain + LangGraph** 实现：`ChatOpenAI` 绑定工具，`StateGraph` 编排「模型 ⇄ 工具」的 ReAct 循环。

## 一、目录结构

```
research-agent/
├── main.py             # 命令行入口（保留，可单独跑）
├── webapp.py          # FastAPI Web 后端：接口 + 静态页托管
├── agent.py           # LangGraph 状态图：模型节点 ⇄ 工具节点，结构化收尾
├── config.py         # 读取 .env / 环境变量 + 护栏参数
├── prompts.py         # 系统提示词（工具 schema 由 @tool 自动生成）
├── storage.py         # SQLite：对话、来源、摘要、轨迹持久化
├── tools/
│   ├── search.py      # 搜索工具（默认 Bing 抓取，可换官方 API）
│   ├── summarize.py   # 页面摘要（模型优先，本地抽取兜底）
│   └── browser.py     # 独立浏览器自动化（Playwright 打开百度结果页并进入第一条）
├── static/
│   └── index.html     # 前端页面（原生 HTML/JS，无构建）
├── data/records.db    # 本地数据库（不进版本库）
├── .env               # 敏感配置（不进版本库）
├── requirements.txt
├── DEPLOY_TENCENT.md  # 腾讯云部署指南
└── README.md
```

## 二、本地运行

### 1. 环境与依赖（Windows PowerShell）

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# 浏览器自动化功能需要下载 Chromium（仅使用浏览器视图时）
python -m playwright install chromium
```

> Linux / macOS 激活：`.venv/bin/activate`。激活脚本被策略拦截时，先执行 `Set-ExecutionPolicy -Scope Process Bypass`。

### 2. 填写模型配置

在项目根目录新建 `.env`（已在 `.gitignore` 排除）：

```env
LLM_API_KEY=你的模型 Key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
```

### 3. 启动 Web 服务

```powershell
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

浏览器打开 <http://127.0.0.1:8000> 即可提问；历史记录自动保存在 `data/records.db`。

### 4. 命令行模式（可选）

```powershell
python main.py "什么是 RAG 检索增强生成"
```

## 三、接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 前端页面 |
| POST | `/api/ask` | 提交问题，跑 Agent 并落库，返回摘要、来源、轨迹 |
| POST | `/api/ask/stream` | SSE 流式：状态 → 来源 → 逐片摘要 → 完成 |
| GET | `/api/history` | 历史记录列表（倒序） |
| GET | `/api/history/{id}` | 单条记录详情（含完整来源与工具轨迹） |
| POST | `/api/browser/open` | **独立功能**：浏览器自动化，直达百度结果页进入第一条，回传标题/链接/正文；不走 Agent、不落库 |

`POST /api/ask` 请求体：

```json
{ "query": "要调研的问题" }
```

`POST /api/browser/open` 请求体：

```json
{ "keyword": "要搜索的关键词" }
```

返回结构：

```json
{
  "ok": true,
  "title": "页面标题",
  "url": "最终落地链接",
  "content": "抽取的正文（截断）",
  "summary": "提炼后的要点摘要（模型优先，失败回退本地抽取）",
  "screenshot_url": "/screenshots/xxx.png",
  "results": [{ "title": "", "url": "", "snippet": "" }],
  "message": "稀疏正文提示或空串"
}
```

调研接口返回结构：

```json
{
  "id": 1,
  "answer": "摘要正文（含 Markdown）",
  "sources": [{ "title": "", "url": "", "snippet": "" }],
  "trace": [{ "turn": 1, "tool": "search_web", "args": {}, "ok": true, "n_results": 5 }],
  "turns": 4,
  "status": "ok",
  "created_at": "2026-11-19T13:20:00"
}
```

## 四、工作流程

```
浏览器提交问题
  → FastAPI 接收，校验配置与入参
  → LangGraph 图：系统提示 + 问题进入 agent 节点（模型绑定工具）
        ├─ 模型返回 tool_calls：进入 tools 节点执行 search_web，回灌 ToolMessage，回到 agent
        └─ 模型无 tool_calls：得到最终摘要
  → 来源去重截断（最多 10 条），连同轨迹写入 SQLite
  → 返回前端渲染摘要与参考来源；历史可回看
```

> 图状态包含 `messages`（消息历史）、`sources`（去重来源）、`trace`（工具轨迹）；新增工具只需在 `agent.py` 用 `@tool` 定义并挂到 `TOOLS` 与 `TOOL_REGISTRY`。

> 浏览器自动化是另一条独立链路：前端「浏览器自动化」视图 → `POST /api/browser/open` → 直接调用 `tools/browser.py`，不进入 Agent 循环、不写调研历史；页面摘要可选调用模型提炼，失败回退本地抽取。

## 五、搜索源与已知边界

- **默认**：Bing 网页结果抓取（免 Key、当前网络可达），解析标题 / 摘要 / 链接。
- **局限**：免 Key 抓取对英文论文、长尾词相关性一般；页面结构变动可能影响解析。
- **替换官方 API**：在 `tools/search.py` 的 `_search_via_tavily` 填入 Tavily / SerpAPI / 官方 Web Search 实现后切换调用，返回结构不变，后端无需改动。

## 五补、独立功能：浏览器自动化（open_first_result）

- **定位**：与资料调研解耦的独立模块，由 `POST /api/browser/open` 直接调用；**不注册为 Agent 工具、流程不由模型决策、不写入调研历史**。前端在工作台顶部「浏览器自动化」标签页单独使用。
- **作用**：用 Playwright 直达百度结果页，依次进入前几条结果，抽取有正文页面的标题、正文与链接；对正文提炼要点摘要，并截取最终落地页回传前端卡片。
- **触发**：用户在浏览器视图输入关键词并点「运行」，固定流程执行，不由模型决策。
- **摘要**：优先调用模型提炼 3~5 条中文要点；无 Key、超时或异常时回退本地抽取式摘要（见 `tools/summarize.py`）。
- **截图**：落地页视口截图存到 `data/screenshots/`，后端以 `/screenshots/<文件名>` 静态路径回传；该目录已被 `.gitignore` 排除，不进版本库。
- **局限**：第一条可能是广告/落地页；JS 重度渲染的站点（SPA）正文可能抓不全；目标站点可能拦截无头浏览器或弹验证码；单次流程约 5~15 秒。
- **可见窗口**：本地默认弹出可见的 Chromium 窗口，能看到打开百度、进入结果的全过程；流程结束自动关闭。想静默后台运行，在 `.env` 设 `BROWSER_HEADLESS=1`。
- **部署**：服务器除装 Python 包外，还需 `playwright install chromium` 与 `install-deps chromium`，且 `BROWSER_HEADLESS=1`，见部署文档 2.1 节。

## 六、部署到腾讯云

见 [DEPLOY_TENCENT.md](./DEPLOY_TENCENT.md)：服务器选购、初始化、systemd 常驻、端口放行、数据备份与加固建议。

## 七、安全说明

- `.env`（含 Key）、`data/`（数据库）、`.venv/` 均已在 `.gitignore` 排除，**不要提交到仓库或外发**。
- Key 只存在后端；前端页面不持有任何密钥。
- 演示版本未加访问鉴权与 HTTPS，公网使用前请按部署文档第八节加固。
