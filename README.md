# Mimo Chat Proxy

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

MiMo AI 免费聊天接口的 OpenAI 兼容本地代理服务器。

将 [小米 MiMo](https://mimo.xiaomi.com) 的免费 AI 接口转换为标准 OpenAI API 格式。

> **⚠️ 仅支持 [Cherry Studio](https://cherry-ai.com) 桌面客户端或 CLI 命令行终端（如 curl）使用，不支持其他 AI 客户端。**

## ✨ 功能特性

- 🔌 **OpenAI 兼容** — 标准 `/v1/chat/completions` 接口，支持 Cherry Studio 和 CLI 终端
- 🔄 **流式输出** — 完整 SSE 流式传输，实时响应
- 🔑 **自动认证** — JWT Token 自动获取与刷新（有效期约 1 小时，过期前自动续期）
- 🔁 **429 自动重试** — 上游限流时自动等待重试，直到恢复
- 🛡️ **本地安全** — 关闭接口仅限 localhost 访问
- 📊 **健康检查** — 内置 `/health` 端点，方便监控

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python mimo_chat.py
```

服务默认运行在 `http://localhost:3001`。

### 3. 配置客户端

> 仅支持 **Cherry Studio** 或 **CLI 命令行终端**（curl / PowerShell / Bash）使用。

#### Cherry Studio（推荐）

设置 → 模型供应商 → 添加自定义供应商：

| 配置项 | 值 |
|--------|-----|
| API 地址 | `http://localhost:3001/v1` |
| API Key | 任意值（如 `sk-any`） |
| 模型名 | `mimo-auto` |

#### CLI 命令行终端

```bash
# 非流式请求
curl http://localhost:3001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 流式请求
curl http://localhost:3001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 4. 测试连通性

```bash
# 检查服务是否运行
curl http://localhost:3001/health

# 查看模型列表
curl http://localhost:3001/v1/models
```

## ⚙️ 配置项

通过环境变量配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `3001` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `OPENAI_URL` | `https://api.xiaomimimo.com/api/free-ai/openai/chat` | 上游聊天接口 |
| `BOOTSTRAP_URL` | `https://api.xiaomimimo.com/api/free-ai/bootstrap` | 上游认证接口 |
| `VERIFY_SSL` | `true` | 是否验证上游 SSL 证书 |
| `LOG_REQUEST_BODY` | `false` | 是否打印请求体（调试用） |
| `MAX_RETRIES` | `999` | 429 最大重试次数 |
| `RETRY_DELAY` | `1` | 重试间隔（秒） |

示例：

```bash
PORT=8080 VERIFY_SSL=true python mimo_chat.py
```

## 📡 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 健康检查 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/models` | 获取模型列表 |
| `POST` | `/v1/chat/completions` | 聊天补全（支持流式） |
| `GET` | `/shutdown` | 关闭服务（仅 localhost） |

## 📁 项目结构

```
mimo-chat-proxy/
├── mimo_chat.py        # 主程序
├── requirements.txt    # Python 依赖
├── LICENSE             # MIT 开源协议
├── README.md           # 项目说明
└── .gitignore          # Git 忽略规则
```

## ⚠️ 兼容性说明

| 客户端 | 支持状态 | 备注 |
|--------|----------|------|
| **Cherry Studio** | ✅ 支持 | 推荐使用，体验最佳 |
| **CLI 终端 (curl)** | ✅ 支持 | 直接调用 API |
| **CLI 终端 (PowerShell)** | ✅ 支持 | `Invoke-RestMethod` 可用 |
| **Lobe Chat** | ❌ 不支持 | |
| **OpenCat** | ❌ 不支持 | |
| **其他 OpenAI 客户端** | ❌ 不支持 | 可能存在兼容性问题 |

## ⚠️ 注意事项

- 本项目仅供学习交流使用
- 仅支持 Cherry Studio 桌面客户端或 CLI 命令行终端
- 上游 JWT Token 有效期约 **1 小时**，代理会在过期前自动刷新，无需手动干预
- 上游接口可能随时变更或关闭
- **禁止**将此服务部署到公网或开放给他人使用
- 不建议在公网环境暴露此服务
- 生产环境请开启 `VERIFY_SSL`

## 📄 License

[MIT](LICENSE)
