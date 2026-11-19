# 腾讯云部署指南（Python 后端 + SQLite）

本指南把本地跑通的 Web Agent 部署到腾讯云轻量服务器，通过公网 IP 访问。
**API Key 只写在服务器后端的 `.env` 里，不进 GitHub、不进前端页面。**

## 一、架构与数据说明

```
浏览器  →  腾讯云服务器（FastAPI + uvicorn）  →  DeepSeek / Bing 搜索
                    │
                    ├─ Playwright 无头 Chromium（浏览器工具：打开百度结果页并进入第一条）
                    └─ SQLite 文件 data/records.db（对话与摘要持久化在服务器磁盘）
```

- 代码仓库（GitHub）：只存源码、前端页面、文档，**不含** `.env`、`data/`、`.venv/`。
- 数据库：单文件 `data/records.db`，备份就是复制这个文件；重装服务器前先备份它。
- 本项目为演示/自用规模，未做鉴权与 HTTPS，上公网前请先看第五节的加固建议。

## 二、选购服务器（还没买）

| 项 | 建议 |
|---|---|
| 产品 | 优先**轻量应用服务器 Lighthouse**（套餐固定、上手简单）；熟悉云主机可选 CVM |
| 配置 | 1 核 2G 起步即可（项目很轻）；后续流量/并发上来再升配 |
| 系统 | Ubuntu 22.04 / 24.04 LTS（本指南命令基于 Ubuntu） |
| 地域 | 选离自己最近的地域即可 |
| 带宽 | 演示用途 3~5 Mbps 套餐够用 |
| 价格 | 以腾讯云控制台实时显示的套餐价为准，本指南不写固定金额 |

> 先用**公网 IP + 端口**访问即可，不需要域名。若要绑定域名且服务器在中国大陆地域，域名需完成 ICP 备案；香港/海外地域免备案但合规性自行评估。

## 三、服务器初始化

1. 在控制台设置或重置服务器登录密码，用 SSH 登录：

```bash
ssh ubuntu@<服务器公网IP>
```

2. 安装基础依赖（Ubuntu）：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## 四、拉代码、配置与启动

### 1. 拉取代码

先把代码推到你的 GitHub 仓库（见 README），然后在服务器上：

```bash
cd /opt
sudo git clone <你的仓库地址> research-agent
cd research-agent
```

### 2. 创建虚拟环境并装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2.1 安装浏览器自动化依赖（浏览器工具需要）

`requirements.txt` 已含 `playwright` Python 包；要跑通 `open_first_result` 浏览器工具，还需在服务器上下载 Chromium 并补齐系统库：

```bash
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium
```

- 仅使用浏览器工具时才需要这一步；不安装时，调用浏览器工具会报浏览器/系统库缺失。
- 无头服务器没有图形界面，Chromium 以 headless 模式运行；`install-deps` 会通过 apt 安装运行所需的系统字体与库。
- 1 核 1G 内存跑浏览器自动化偏紧，建议至少 1 核 2G；单次浏览器流程约 5~15 秒。

### 3. 写后端配置（敏感信息，不进仓库）

```bash
cat > .env <<'EOF'
LLM_API_KEY=你的模型Key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
EOF
```

### 4. 手动验证一次（前台运行，便于看报错）

```bash
.venv/bin/python -m uvicorn webapp:app --host 0.0.0.0 --port 8000
```

另开终端验证：

```bash
curl http://127.0.0.1:8000/api/history
```

返回 JSON 即后端正常；`Ctrl+C` 退出。

## 五、进程守护与端口放行

### 1. 用 systemd 常驻进程（推荐）

创建 `/etc/systemd/system/research-agent.service`：

```ini
[Unit]
Description=Research Agent Web
After=network.target

[Service]
WorkingDirectory=/opt/research-agent
ExecStart=/opt/research-agent/.venv/bin/python -m uvicorn webapp:app --host 0.0.0.0 --port 8000
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable research-agent
sudo systemctl start research-agent
sudo systemctl status research-agent   # 看是否 active (running)
```

看日志排查：

```bash
journalctl -u research-agent -f
```

### 2. 放行端口

- **轻量服务器**：在控制台「防火墙」中放行 TCP 8000 端口。
- **CVM**：在安全组入站规则中放行 TCP 8000。
- 本机 Ubuntu 防火墙（若启用 ufw）：

```bash
sudo ufw allow 8000/tcp
```

### 3. 浏览器访问

```
http://<服务器公网IP>:8000
```

## 六、数据备份与迁移

- 对话与摘要都在 `data/records.db`。
- 备份：停服后复制该文件，或直接下载：

```bash
cp data/records.db data/records.db.bak
```

- 迁移到新服务器：连同代码目录一起拷走 `data/records.db`，`.env` 重新填写。

## 七、常见问题

| 现象 | 排查 |
|---|---|
| 浏览器打不开页面 | 先 `curl 127.0.0.1:8000` 看本机是否通；不通查服务日志；本机通但外部不通，查防火墙/安全组端口放行 |
| 接口返回 503 缺少配置 | 服务器上 `.env` 没写或没被读取 |
| 接口返回 502 模型调用失败 | 查 Key 是否有效、额度、网络能否访问模型端点 |
| 搜索结果为空/不相关 | 免 Key 的 Bing 抓取覆盖有限，可按 `tools/search.py` 说明接入官方搜索 API |
| 浏览器工具超时/打不开页面 | 服务器先执行 `playwright install chromium` 和 `install-deps chromium`；无头环境下目标站点可能拦截或弹验证码，动态渲染页面正文可能抓不全 |
| 服务重启后退出 | 确认用 systemd 常驻，而不是前台 `nohup` 后断开 SSH |

## 八、上生产前的加固建议（按需）

1. **Nginx 反向代理 + HTTPS**：80/443 端口对外，后端仍监听 8000，用证书加 HTTPS。
2. **访问鉴权**：加登录口令或接口 Token，避免被他人刷接口消耗额度。
3. **稳定搜索源**：接入 Tavily / SerpAPI / 官方 Web Search。
4. **定时备份**：把 `records.db` 定期备份到对象存储 COS。
5. **资源监控**：关注内存与磁盘，SQLite 单库过大后再考虑迁移 MySQL/Redis。
