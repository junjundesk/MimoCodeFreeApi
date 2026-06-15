"""
Mimo Chat Proxy - MiMo AI 免费聊天接口的 OpenAI 兼容本地代理

将小米 MiMo 的免费 AI 接口转换为标准 OpenAI API 格式，
可无缝对接 Cherry Studio、Lobe Chat、OpenCat 等主流 AI 客户端。
"""

import base64
import json
import logging
import os
import secrets
import string
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any

import requests
import urllib3

# 禁用不安全请求警告（用于 verify=False 场景）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mimo-proxy")

# ---------------------------------------------------------------------------
# 配置（通过环境变量覆盖）
# ---------------------------------------------------------------------------
OPENAI_URL: str = os.getenv(
    "OPENAI_URL",
    "https://api.xiaomimimo.com/api/free-ai/openai/chat",
)
BOOTSTRAP_URL: str = os.getenv(
    "BOOTSTRAP_URL",
    "https://api.xiaomimimo.com/api/free-ai/bootstrap",
)
VERIFY_SSL: bool = os.getenv("VERIFY_SSL", "false").lower() == "true"
LOG_REQUEST_BODY: bool = os.getenv("LOG_REQUEST_BODY", "false").lower() == "true"
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "999"))
RETRY_DELAY: float = float(os.getenv("RETRY_DELAY", "1"))
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "3001"))

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CLIENT_ID: str = secrets.token_hex(32)
SESSION_ALPHABET: str = string.ascii_letters + string.digits
_UPSTREAM_429_DELAY_DEFAULT: float = 1.0

# JWT 缓存
_jwt_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}
_jwt_lock: threading.Lock = threading.Lock()

# 上游请求头模板
_HEADERS_TEMPLATE: dict[str, str] = {
    "User-Agent": (
        "mimocode/0.1.0 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
    ),
    "X-Mimo-Source": "mimocode-cli-free",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Content-Type": "application/json",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _random_session_affinity() -> str:
    """生成随机 session affinity token"""
    return "ses_" + "".join(
        secrets.choice(SESSION_ALPHABET) for _ in range(26)
    )


def _get_jwt_expires_at(jwt_token: str) -> float:
    """从 JWT 中解析过期时间"""
    try:
        payload = jwt_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except Exception:
        return time.time() + 300


def _get_jwt() -> str:
    """获取 JWT Token，带缓存和双重检查锁"""
    now = time.time()
    if _jwt_cache["token"] and _jwt_cache["expires_at"] - now > 60:
        return _jwt_cache["token"]

    with _jwt_lock:
        now = time.time()
        if _jwt_cache["token"] and _jwt_cache["expires_at"] - now > 60:
            return _jwt_cache["token"]

        logger.info("正在获取新的 JWT Token...")
        resp = requests.post(
            BOOTSTRAP_URL,
            headers={"Content-Type": "application/json"},
            json={"client": CLIENT_ID},
            timeout=30,
            verify=VERIFY_SSL,
        )
        resp.raise_for_status()
        jwt_token: str = resp.json()["jwt"]
        _jwt_cache["token"] = jwt_token
        _jwt_cache["expires_at"] = _get_jwt_expires_at(jwt_token)
        logger.info("JWT Token 获取成功")
        return jwt_token


def _build_upstream_headers() -> dict[str, str]:
    """构建上游请求头"""
    headers = _HEADERS_TEMPLATE.copy()
    headers["x-session-affinity"] = _random_session_affinity()
    headers["Authorization"] = f"Bearer {_get_jwt()}"
    return headers


def _retry_after_seconds(resp: requests.Response) -> float:
    """从 429 响应中解析重试等待时间"""
    try:
        return max(float(resp.headers.get("Retry-After", RETRY_DELAY)), 0.5)
    except ValueError:
        return RETRY_DELAY


# ---------------------------------------------------------------------------
# 上游请求
# ---------------------------------------------------------------------------
def post_upstream(payload: dict[str, Any]) -> requests.Response:
    """向上游发送请求，429 自动重试"""
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.post(
            OPENAI_URL,
            headers=_build_upstream_headers(),
            json=payload,
            stream=True,
            timeout=60,
            verify=VERIFY_SSL,
        )
        if resp.status_code != 429:
            return resp
        if attempt == MAX_RETRIES:
            return resp

        delay = _retry_after_seconds(resp)
        logger.warning(
            "上游 429 限流，%.1fs 后重试 (%d/%d)",
            delay,
            attempt + 1,
            MAX_RETRIES,
        )
        resp.close()
        time.sleep(delay)

    # 理论上不会到这里，但类型检查需要
    raise RuntimeError("Unexpected retry loop exit")


# ---------------------------------------------------------------------------
# HTTP 请求处理
# ---------------------------------------------------------------------------
class ProxyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容代理请求处理器"""

    protocol_version = "HTTP/1.1"

    # ---- CORS 预检 ----
    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    # ---- GET ----
    def do_GET(self) -> None:
        if self.path in ("/", "/health"):
            self._handle_health()
        elif self.path == "/shutdown":
            self._handle_shutdown()
        elif self.path.startswith("/v1/models"):
            self._handle_models()
        else:
            self._send_json_error(404, "not found")

    # ---- POST ----
    def do_POST(self) -> None:
        if self.path.startswith("/v1/models"):
            self._handle_models()
        elif self.path == "/v1/chat/completions":
            self._handle_chat()
        else:
            self._send_json_error(404, "not found")

    # ---- 工具方法 ----
    def _send_cors_headers(self) -> None:
        """发送 CORS 响应头"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )

    def _send_json_response(
        self, status: int, data: dict[str, Any], cors: bool = False
    ) -> None:
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if cors:
            self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_json_error(self, status: int, message: str) -> None:
        """发送 JSON 错误响应"""
        self._send_json_response(
            status, {"error": {"message": message}}, cors=True
        )

    # ---- 端点处理 ----
    def _handle_health(self) -> None:
        """健康检查"""
        self._send_json_response(200, {"status": "ok"}, cors=True)

    def _handle_shutdown(self) -> None:
        """关闭服务（仅 localhost 可用）"""
        client_ip = self.client_address[0]
        if client_ip not in ("127.0.0.1", "::1"):
            self._send_json_error(403, "forbidden")
            return

        self._send_json_response(200, {"status": "shutting down"})
        logger.info("收到关闭请求，正在关闭服务器...")
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _handle_models(self) -> None:
        """返回模型列表"""
        model_obj = {
            "id": "mimo-auto",
            "object": "model",
            "created": 1714982195,
            "owned_by": "mimo",
            "name": "mimo-auto",
            "status": "Active",
        }
        path_parts = self.path.strip("/").split("/")

        if len(path_parts) == 3:
            # /v1/models/{id}
            self._send_json_response(200, model_obj, cors=True)
        else:
            # /v1/models
            self._send_json_response(
                200, {"object": "list", "data": [model_obj]}, cors=True
            )

    def _handle_chat(self) -> None:
        """处理聊天补全请求"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        payload: dict[str, Any] = json.loads(body)

        logger.info("收到请求: %s, bytes=%d", self.path, content_length)
        if LOG_REQUEST_BODY:
            logger.info("  Headers: %s", dict(self.headers))
            logger.info(
                "  Body: %s", json.dumps(payload, indent=2, ensure_ascii=False)
            )

        # 确保流式输出
        payload["stream"] = True
        payload["model"] = "mimo-auto"
        payload.setdefault("max_tokens", 128000)
        payload.setdefault("stream_options", {"include_usage": True})

        # 保留前端 system 消息，没有则添加默认的
        messages = payload.get("messages", [])
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            payload["messages"] = [
                {
                    "role": "system",
                    "content": "You are a title generator. You output ONLY a thread title.",
                }
            ] + messages

        logger.info(
            "  -> 上游: model=%s, stream=%s",
            payload["model"],
            payload["stream"],
        )

        try:
            resp = post_upstream(payload)
        except Exception as exc:
            logger.error("上游请求异常: %s", exc)
            self._write_sse_error(502, str(exc))
            return

        if resp.status_code != 200:
            logger.error("上游返回错误: %d", resp.status_code)
            try:
                error = resp.json().get("error", {})
                message = error.get("message") or resp.text
            except ValueError:
                message = resp.text
            self._write_sse_error(resp.status_code, message)
            return

        # 透传 SSE 流
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()

        for line in resp.iter_lines(decode_unicode=False):
            if line and line.startswith(b"data: ") and line != b"data: [DONE]":
                self.wfile.write(line + b"\n\n")
                self.wfile.flush()
        resp.close()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _write_sse_error(self, status_code: int, message: str) -> None:
        """以 SSE 格式写入错误信息"""
        error_data = {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": f"上游返回错误 {status_code}: {message}"},
                    "finish_reason": None,
                }
            ]
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(
            ("data: " + json.dumps(error_data, ensure_ascii=False) + "\n\n").encode()
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        """自定义日志格式"""
        logger.info("[Proxy] %s %s %s", args[0], args[1], args[2])


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    """启动代理服务器"""
    logger.info("=" * 50)
    logger.info("Mimo Chat Proxy 启动中...")
    logger.info("监听地址: http://%s:%d", HOST, PORT)
    logger.info("上游接口: %s", OPENAI_URL)
    logger.info("SSL 验证: %s", "开启" if VERIFY_SSL else "关闭")
    logger.info("兼容 OpenAI 格式: POST /v1/chat/completions")
    logger.info("按 Ctrl+C 停止服务器")
    logger.info("=" * 50)

    server = ThreadingHTTPServer((HOST, PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("正在关闭服务器...")
        server.server_close()
        logger.info("服务器已关闭")


if __name__ == "__main__":
    main()
