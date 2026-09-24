"""局域网极简聊天：一台电脑运行本脚本，两台都用浏览器打开同一网址，收发文本和文件。

只依赖 Python 标准库。聊天记录存 data/messages.jsonl，文件存 data/files/。
"""
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("LANCHAT_PORT", 8765))
DISCOVERY_PORT = PORT + 1
BASE = Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", BASE))  # 打包成 exe 后，index.html 解压在 _MEIPASS 下
# exe 所在目录可能随手放在桌面/下载，数据统一放 %APPDATA%\LanChat；脚本运行时仍放在脚本旁
DATA = Path(os.environ["APPDATA"]) / "LanChat" if getattr(sys, "frozen", False) else BASE / "data"
FILES = DATA / "files"
LOG = DATA / "messages.jsonl"
PIN_FILE = DATA / "pin.txt"
INDEX = RES / "index.html"
MAX_UPLOAD = 4 * 1024**3  # 单文件上限 4 GB
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

FILES.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
messages = []  # 消息 id 从 1 递增，messages[id - 1] 即该消息

if LOG.exists():
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    for i, m in enumerate(messages, 1):
        m["id"] = i

if PIN_FILE.exists():
    PIN = PIN_FILE.read_text(encoding="utf-8").strip()
else:
    PIN = f"{secrets.randbelow(10000):04d}"
    PIN_FILE.write_text(PIN, encoding="utf-8")


def add(msg):
    with lock:
        msg["id"] = len(messages) + 1
        msg["ts"] = time.time()
        messages.append(msg)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def public(msg):
    return {k: v for k, v in msg.items() if k != "path"}


def safe_name(name):
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "未命名"


def same_pin(value):
    return secrets.compare_digest(str(value).encode(), PIN.encode())


def stored_path(mid):
    """本机主机模式下，消息 mid 对应文件在磁盘上的位置；不是文件消息返回 None。"""
    with lock:
        m = messages[mid - 1] if 0 < mid <= len(messages) else None
    if m and m["type"] == "file" and (FILES / m["path"]).exists():
        return FILES / m["path"]
    return None


def lan_ips():
    ips = []
    try:  # 默认路由所在网卡排第一
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(ips) if not ip.startswith("127.")]


class Handler(BaseHTTPRequestHandler):
    server_version = "LanChat"

    def log_message(self, *args):
        pass

    def local(self):
        return self.client_address[0] == "127.0.0.1"

    def authed(self):
        if self.local():  # 本机访问免访问码
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return "pin" in cookie and same_pin(cookie["pin"].value)

    def send_json(self, obj, status=200, headers=()):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > 10 * 1024**2:
            raise ValueError("消息过长")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.authed():
            return self.send_json({"error": "pin"}, 401)
        if url.path == "/api/messages":
            since = int(urllib.parse.parse_qs(url.query).get("since", ["0"])[0])
            with lock:
                new = [public(m) for m in messages[max(since, 0):]]
            return self.send_json({"messages": new})
        if url.path == "/api/info":  # 主机窗口顶部显示本机地址和访问码，供另一台连接
            if not self.local():
                return self.send_json({})
            return self.send_json({"addrs": [f"{ip}:{PORT}" for ip in lan_ips()], "pin": PIN})
        if url.path.startswith("/files/"):
            return self.send_file(url)
        self.send_error(404)

    def send_file(self, url):
        try:
            mid = int(url.path.rsplit("/", 1)[1])
        except ValueError:
            return self.send_error(404)
        with lock:
            m = messages[mid - 1] if 0 < mid <= len(messages) else None
        if not m or m["type"] != "file" or not (FILES / m["path"]).exists():
            return self.send_error(404)
        path = FILES / m["path"]
        inline = "inline" in url.query
        quoted = urllib.parse.quote(m["name"])
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(m["name"])[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quoted}")
        self.send_header("X-Content-Type-Options", "nosniff")
        # 在页面内预览时放进沙箱：即使对方发来 html/svg，脚本也无法以本站身份运行
        self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile, 1024**2)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/login":
            if same_pin(self.read_json().get("pin", "")):
                cookie = f"pin={PIN}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict"
                return self.send_json({"ok": True}, headers=[("Set-Cookie", cookie)])
            return self.send_json({"error": "pin"}, 403)
        if not self.authed():
            return self.send_json({"error": "pin"}, 401)
        if path == "/api/text":
            data = self.read_json()
            text = str(data.get("text", ""))
            if not text.strip():
                return self.send_json({"error": "empty"}, 400)
            msg = add({"type": "text", "text": text, "client": str(data.get("client", ""))[:64],
                       "ip": self.client_address[0]})
            return self.send_json(public(msg))
        if path == "/api/file":
            return self.recv_file()
        self.send_error(404)

    def recv_file(self):
        n = int(self.headers.get("Content-Length", -1))
        if not 0 <= n <= MAX_UPLOAD:
            return self.send_json({"error": "size"}, 413)
        name = safe_name(urllib.parse.unquote(self.headers.get("X-Filename", "")))
        stored = f"{secrets.token_hex(4)}_{name}"
        tmp = FILES / f".part-{stored}"
        try:
            with tmp.open("wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(left, 1024**2))
                    if not chunk:
                        raise ConnectionError("上传中断")
                    f.write(chunk)
                    left -= len(chunk)
            tmp.rename(FILES / stored)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        msg = add({"type": "file", "name": name, "size": n, "path": stored,
                   "image": Path(name).suffix.lower() in IMAGE_EXT,
                   "client": self.headers.get("X-Client", "")[:64], "ip": self.client_address[0]})
        return self.send_json(public(msg))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):  # 浏览器中途关闭/取消上传，不必打印
            return
        super().handle_error(request, client_address)


def answer_discovery():
    """回应局域网广播 "LANCHAT?"，让另一台 app 自动找到本机。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", DISCOVERY_PORT))
        while True:
            data, addr = s.recvfrom(64)
            if data == b"LANCHAT?":
                s.sendto(f"LANCHAT {PORT}".encode(), addr)


def start():
    """后台线程启动服务和广播应答；端口被占用时抛 OSError。"""
    server = Server(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=answer_discovery, daemon=True).start()
    return server


if __name__ == "__main__":
    try:
        server = Server(("0.0.0.0", PORT), Handler)
    except OSError as e:
        sys.exit(f"端口 {PORT} 无法使用（可能已经在运行）：{e}")
    threading.Thread(target=answer_discovery, daemon=True).start()
    print("局域网传输已启动，两台电脑的浏览器都打开下面任一地址：")
    for ip in lan_ips():
        print(f"    http://{ip}:{PORT}")
    print(f"本机也可用 http://localhost:{PORT}")
    print(f"访问码：{PIN}（每个浏览器只需输入一次）")
    print("关闭此窗口即停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
