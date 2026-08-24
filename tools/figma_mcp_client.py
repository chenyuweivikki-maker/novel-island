#!/usr/bin/env python3
"""
figma_mcp_client.py — 小说岛 Figma MCP 客户端（官方 figma-developer-mcp 方案）

把你在 Figma 里设计好的 UI 拉回来：设计稿 → 本客户端读取（变量/样式/节点/codegen/截图）→ 落盘 → 前端实现。

与 Claude Desktop 共用同一个 MCP server（figma-developer-mcp）：
  - 密钥运行时从 Claude Desktop 配置读取（单一来源，不复制进仓库）
  - 也可以环境变量 FIGMA_API_KEY 或 ~/.figma-mcp/token 文件

用法：
  # 1. 连通性自检（握手 + 列出可用工具）
  python3 figma_mcp_client.py --ping

  # 2. 拉取整份文件数据（页面/Frame 层级 + 内容 + 组件信息，含设计变量）
  python3 figma_mcp_client.py --file <file_key> --get-data --output design/figma/file-data.json

  # 3. 拉取某个节点的数据（URL 带 node-id 时用它精确定位）
  python3 figma_mcp_client.py --file <file_key> --node "1:234" --get-data --output design/figma/node.json

  # 4. 批量下载节点图片到工作区 design/figma-images/（PNG @2x，含 SVG 图标）
  python3 figma_mcp_client.py --file <file_key> --images --nodes "1:2,1:5,123:4" --local-path design/figma-images

file_key 从 Figma 文件链接拿：https://www.figma.com/design/<file_key>/xxx
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ─── 常量 ────────────────────────────────────────────────────────────────────

CLAUDE_CONFIG_CANDIDATES = [
    Path.home() / "Library/Application Support/Claude/claude_desktop_config.json",
    Path.home() / "Library/Application Support/Claude-3p/claude_desktop_config.json",
]
MCP_SERVER = "figma-developer-mcp"
TOKEN_FILE = Path.home() / ".figma-mcp" / "token"

NPX_CANDIDATES = [
    "/opt/homebrew/bin/npx",
    "/usr/local/bin/npx",
    "/opt/local/bin/npx",
]


# ─── 密钥解析（绝不打印密钥本身）───────────────────────────────────────────────

def load_figma_token() -> str:
    """优先级：环境变量 > Claude Desktop 配置 > ~/.figma-mcp/token"""
    env_token = os.environ.get("FIGMA_API_KEY")
    if env_token:
        return env_token

    for cfg_path in CLAUDE_CONFIG_CANDIDATES:
        if not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
            srv = cfg.get("mcpServers", {}).get(MCP_SERVER, {})
            token = (srv.get("env") or {}).get("FIGMA_API_KEY", "")
            if token:
                return token
        except Exception:
            continue

    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()

    raise RuntimeError(
        "未找到 Figma API Key：请设置环境变量 FIGMA_API_KEY，"
        "或确保 Claude Desktop 已配置 figma-developer-mcp，"
        f"或把密钥写入 {TOKEN_FILE}（chmod 600）"
    )


def find_npx() -> str:
    for cand in NPX_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return "npx"  # 最后尝试 PATH


# ─── MCP stdio 客户端 ────────────────────────────────────────────────────────

class McpStdioClient:
    def __init__(self, token: str, timeout: float = 60.0):
        self.token = token
        self.timeout = timeout
        self._id = 0
        self.proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        env = os.environ.copy()
        env["FIGMA_API_KEY"] = self.token
        env.setdefault("FRAMELINK_TELEMETRY", "off")
        npx_bin = find_npx()
        npx_dir = os.path.dirname(npx_bin)
        if npx_dir and npx_dir not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = npx_dir + os.pathsep + env.get("PATH", "")
        # npm 缓存目录：默认用项目根 .npx-cache（避开 ~/.npm 的 root 权限残留问题）
        cache_dir = os.environ.get(
            "NPX_CACHE_DIR", str(Path(__file__).resolve().parents[2] / ".npx-cache")
        )
        self.proc = subprocess.Popen(
            [npx_bin, "-y", "--cache", cache_dir, MCP_SERVER, "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        # 握手：initialize → initialized
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dsh-figma-bridge", "version": "0.1.0"},
        })
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
            except Exception:
                pass

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _notify(self, method: str, params: dict) -> None:
        assert self.proc and self.proc.stdin
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: Optional[float] = None) -> dict:
        assert self.proc and self.proc.stdout
        req_id = self._next_id()
        line = json.dumps({
            "jsonrpc": "2.0", "id": req_id, "method": method, "params": params,
        })
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.proc.stdout], [], [], 0.5)
            if not r:
                continue
            raw = self.proc.stdout.readline()
            if not raw:
                # server 可能把错误写到了 stderr
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"MCP server 退出或无响应：{err[:500]}")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # 忽略非 JSON 行（日志等）
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"MCP 错误：{msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"MCP 请求超时：{method}")

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict, timeout: Optional[float] = None) -> dict:
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if result.get("isError"):
            texts = extract_text(result.get("content", []))
            raise RuntimeError(f"工具 {name} 调用失败：{texts}")
        return result

    @staticmethod
    def extract_text(result: dict) -> str:
        content = result.get("content", [])
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)


def extract_text(content: list) -> str:
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


# ─── 落地辅助 ─────────────────────────────────────────────────────────────────

def save_output(text: str, output: Optional[str], label: str = "结果") -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"✅ {label}已保存到 {path}", file=sys.stderr)
    else:
        print(text)


def download_image(url: str, output: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-figma-bridge"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"✅ 图片已保存到 {path}", file=sys.stderr)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="小说岛 Figma MCP 客户端")
    parser.add_argument("--ping", action="store_true", help="连通性自检：握手 + 列出工具")
    parser.add_argument("--tools", action="store_true", help="列出 MCP 可用工具")
    parser.add_argument("--file", dest="file_key", help="Figma 文件 key（URL 中 /design/<key>/ 段）")
    parser.add_argument("--node", dest="node_id", help="节点 ID（形如 1:234，URL 参数 node-id=）")
    parser.add_argument("--depth", type=int, help="遍历深度（默认不限，服务器自动控制）")
    parser.add_argument("--get-data", action="store_true", help="拉取文件/节点数据（get_figma_data）")
    parser.add_argument("--images", action="store_true", help="批量下载节点图片（配合 --nodes）")
    parser.add_argument("--nodes", help="图片节点 ID 列表，逗号分隔，如 1:2,1:5,123:4")
    parser.add_argument("--local-path", default="design/figma-images",
                        help="图片保存目录（相对工作区根，默认 design/figma-images）")
    parser.add_argument("--png-scale", type=float, default=2.0, help="PNG 导出倍率（默认 2）")
    parser.add_argument("--output", help="输出文件路径（数据模式）")
    args = parser.parse_args()

    token = load_figma_token()
    client = McpStdioClient(token)
    try:
        client.start()

        if args.ping or args.tools:
            tools = client.list_tools()
            print(f"✅ 已连接 figma-developer-mcp，可用工具 {len(tools)} 个：")
            for t in tools:
                print(f"  - {t.get('name')}: {t.get('description','')[:80]}")
            return

        if not args.file_key:
            parser.error("需要 --file <file_key>（Figma 链接里的 key）")

        if args.images:
            if not args.nodes:
                parser.error("--images 需要 --nodes '1:2,1:5,...'")
            nodes = [
                {"nodeId": n, "fileName": f"node-{n.replace(':', '-')}.png"}
                for n in args.nodes.split(",") if n.strip()
            ]
            result = client.call_tool("download_figma_images", {
                "fileKey": args.file_key,
                "nodes": nodes,
                "localPath": args.local_path,
                "pngScale": args.png_scale,
            }, timeout=300)
            print(client.extract_text(result))
            print(f"✅ 图片已保存到工作区 {args.local_path}/", file=sys.stderr)

        elif args.get_data:
            params: dict = {"fileKey": args.file_key}
            if args.node_id:
                params["nodeId"] = args.node_id
            if args.depth:
                params["depth"] = args.depth
            result = client.call_tool("get_figma_data", params, timeout=300)
            save_output(
                json.dumps(result, ensure_ascii=False, indent=2),
                args.output,
                "数据",
            )

        else:
            parser.print_help()

    finally:
        client.stop()


if __name__ == "__main__":
    main()
