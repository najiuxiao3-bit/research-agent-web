# 资料查询总结助手（Research Agent · Web 版）

一个从 0 到 1 的最小可交互 Agent：**浏览器输入问题 → 后端联网检索 → 提炼带来源的中文摘要 → 对话与摘要存入 SQLite**。
代码按「配置 / 提示词 / 工具 / Agent 循环 / 存储 / Web 接口 / 前端」分层，便于照着改和扩展。

## 一、目录结构

```
research-agent/
├── main.py             # 命令行入口（保留，可单独跑）
├── webapp.py          # FastAPI Web 后端：接口 + 静态页托管
├── agent.py           # Agent 主循环：调模型 → 工具决策 → 回灌 → 结构化收尾
├── config.py         # 读取 .env / 环境变量 + 护栏参数
├── prompts.py         # 系统提示词 + 工具 Schema
├── storage.py         # SQLite：对话、来源、摘要、轨迹持久化
├── tools/
│   ├── search.py      # 搜索工具（默认 Bing 抓取，可换官方 API）
│   └── browser.py     # 浏览器工具（Playwright 打开百度结果页并进入第一条）
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
# 浏览器工具需要下载 Chromium（仅使用 open_first_result 时）
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
uvicorn webapp:app --host 127.0.0.1 --port 8000
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
| GET | `/api/history` | 历史记录列表（倒序） |
| GET | `/api/history/{id}` | 单条记录详情（含完整来源与工具轨迹） |

`POST /api/ask` 请求体：

```json
{ "query": "要调研的问题" }
```

返回结构：

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
  → Agent 循环：系统提示 + 问题 → 调模型
        ├─ tool_calls：执行 search_web / open_first_result，结果回灌，继续循环
        └─ 无 tool_calls：得到最终摘要
  → 来源去重截断（最多 10 条），连同轨迹写入 SQLite
  → 返回前端渲染摘要与参考来源；历史可回看
```

## 五、搜索源与已知边界

- **默认**：Bing 网页结果抓取（免 Key、当前网络可达），解析标题 / 摘要 / 链接。
- **局限**：免 Key 抓取对英文论文、长尾词相关性一般；页面结构变动可能影响解析。
- **替换官方 API**：在 `tools/search.py` 的 `_search_via_tavily` 填入 Tavily / SerpAPI / 官方 Web Search 实现后切换调用，返回结构不变，后端无需改动。

## 五补、浏览器工具（open_first_result）

- **作用**：用 Playwright 直达百度结果页，依次进入前几条结果，抽取有正文页面的标题、正文与链接。
- **触发**：模型判断需要查看某条结果完整页面时自动调用；正文稀疏时 Agent 会自动回退 `search_web` 补资料。
- **局限**：第一条可能是广告/落地页；JS 重度渲染的站点（SPA）正文可能抓不全；目标站点可能拦截无头浏览器或弹验证码；单次流程约 5~15 秒。
- **可见窗口**：本地默认弹出可见的 Chromium 窗口，能看到打开百度、进入结果的全过程；流程结束自动关闭。想静默后台运行，在 `.env` 设 `BROWSER_HEADLESS=1`。
- **部署**：服务器除装 Python 包外，还需 `playwright install chromium` 与 `install-deps chromium`，见部署文档 2.1 节。

## 六、部署到腾讯云

见 [DEPLOY_TENCENT.md](./DEPLOY_TENCENT.md)：服务器选购、初始化、systemd 常驻、端口放行、数据备份与加固建议。

## 七、安全说明

- `.env`（含 Key）、`data/`（数据库）、`.venv/` 均已在 `.gitignore` 排除，**不要提交到仓库或外发**。
- Key 只存在后端；前端页面不持有任何密钥。
- 演示版本未加访问鉴权与 HTTPS，公网使用前请按部署文档第八节加固。
