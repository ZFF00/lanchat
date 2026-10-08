"""局域网极简聊天：一台电脑运行本脚本，两台都用浏览器打开同一网址，收发文本和文件。

只依赖 Python 标准库。聊天记录存 data/messages.jsonl，文件存 data/files/。
"""
import hashlib
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
DATA = Path(os.environ.get("LANCHAT_DATA") or DATA)  # 测试时让两个实例用各自的数据目录
FILES = DATA / "files"
LOG = DATA / "messages.jsonl"
PIN_FILE = DATA / "pin.txt"
INDEX = RES / "index.html"
MAX_UPLOAD = 4 * 1024**3  # 单文件上限 4 GB
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

FILES.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
# 按时间排序，messages[id - 1] 即该消息；uid 是跨设备不变的消息编号，两台同步记录时靠它去重
messages = []
by_uid = {}
# 同步插入了更早的消息、顺序变了时换一个值，页面据此整页重新加载
REV = secrets.token_hex(4)
SYNC_FIELDS = ("uid", "ts", "type", "text", "name", "size", "image", "client", "ip")


def legacy_uid(m):
    """v1.0.7 及更早的消息没有 uid，按内容算一个固定值。"""
    key = f'{m.get("ts")}|{m.get("client")}|{m.get("type")}|{m.get("text") or m.get("name")}|{m.get("size")}'
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def save_log():
    """整份重写聊天记录（调用方持有 lock）。先写临时文件再替换，中途断电不会丢记录。"""
    tmp = LOG.with_name(LOG.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    tmp.replace(LOG)


def reindex():
    for i, m in enumerate(messages, 1):
        m["id"] = i


if LOG.exists():
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    messages.sort(key=lambda m: m.get("ts", 0))
    legacy = [m for m in messages if not m.get("uid")]
    for m in legacy:
        m["uid"] = legacy_uid(m)
    reindex()
    by_uid = {m["uid"]: m for m in messages}
    if legacy:  # 一次性迁移：补上 uid 后重写，原记录留一份备份
        backup = LOG.with_name(LOG.name + ".bak")
        if not backup.exists():
            shutil.copyfile(LOG, backup)
        save_log()

if PIN_FILE.exists():
    PIN = PIN_FILE.read_text(encoding="utf-8").strip()
else:
    PIN = f"{secrets.randbelow(10000):04d}"
    PIN_FILE.write_text(PIN, encoding="utf-8")

# 设备编号：首次运行随机生成，之后固定；连接端靠它认出 IP 变了的同一台主机
DEVICE_FILE = DATA / "device_id.txt"
if DEVICE_FILE.exists():
    DEVICE_ID = DEVICE_FILE.read_text(encoding="utf-8").strip()
else:
    DEVICE_ID = secrets.token_hex(8)
    DEVICE_FILE.write_text(DEVICE_ID, encoding="utf-8")
HOSTNAME = socket.gethostname()


def add(msg):
    with lock:
        msg["id"] = len(messages) + 1
        msg["ts"] = time.time()
        msg["uid"] = secrets.token_hex(8)
        messages.append(msg)
        by_uid[msg["uid"]] = msg
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def has_file(m):
    return m["type"] == "file" and bool(m.get("path")) and (FILES / m["path"]).exists()


def public(msg):
    out = {k: v for k, v in msg.items() if k != "path"}
    if msg["type"] == "file":
        out["has"] = has_file(msg)  # 同步过来的文件，本体可能还在路上
    return out


def import_messages(items):
    """并入另一台的消息：按 uid 去重、按时间插入。文件消息先只建条目，本体由 attach_file 补上。"""
    global REV
    added, reordered = 0, False
    with lock:
        for it in items:
            uid = str(it.get("uid", ""))[:32]
            if not uid or uid in by_uid or it.get("type") not in ("text", "file"):
                continue
            m = {k: it[k] for k in SYNC_FIELDS if k in it}
            m["uid"], m["ts"] = uid, float(m.get("ts") or time.time())
            if m["type"] == "file":
                m["name"], m["path"] = safe_name(str(m.get("name", ""))), None
            i = len(messages)
            while i and messages[i - 1]["ts"] > m["ts"]:
                i -= 1
            reordered |= i < len(messages)
            messages.insert(i, m)
            by_uid[uid] = m
            added += 1
        if added:
            if reordered:
                REV = secrets.token_hex(4)
            reindex()
            save_log()
    return added


def attach_file(uid, tmp):
    """把同步来的文件本体（已写好的临时文件 tmp）挂到对应消息上；消息不存在或已有文件则丢弃。"""
    global REV
    with lock:
        m = by_uid.get(uid)
        if not m or m["type"] != "file" or has_file(m):
            tmp.unlink(missing_ok=True)
            return False
        stored = f"{secrets.token_hex(4)}_{m['name']}"
        tmp.replace(FILES / stored)
        m["path"] = stored
        REV = secrets.token_hex(4)  # 让页面刷新，"同步中"的文件卡片变成可打开
        save_log()
    return True


def path_by_uid(uid):
    with lock:
        m = by_uid.get(uid)
        return FILES / m["path"] if m and has_file(m) else None


def sync_view():
    with lock:
        return [public(m) for m in messages]


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
    if m and has_file(m):
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
        if url.path == "/api/device":  # 不需访问码：与局域网广播应答公开的内容相同
            return self.send_json({"id": DEVICE_ID, "hostname": HOSTNAME})
        if not self.authed():
            return self.send_json({"error": "pin"}, 401)
        if url.path == "/api/messages":
            since = int(urllib.parse.parse_qs(url.query).get("since", ["0"])[0])
            with lock:
                new = [public(m) for m in messages[max(since, 0):]]
            return self.send_json({"messages": new, "rev": REV})
        if url.path == "/api/sync":  # 另一台拉取全部记录（含 uid、文件是否在本机）
            return self.send_json({"device": DEVICE_ID, "messages": sync_view()})
        if url.path.startswith("/api/sync/file/"):
            path = path_by_uid(url.path.rsplit("/", 1)[1])
            if not path:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, 1024**2)
            return
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
        if not m or not has_file(m):
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
        if path == "/api/sync":  # 另一台推送它有、本机没有的记录
            items = self.read_json().get("messages", [])
            return self.send_json({"added": import_messages(items if isinstance(items, list) else [])})
        if path.startswith("/api/sync/file/"):
            return self.recv_sync_file(path.rsplit("/", 1)[1])
        self.send_error(404)

    def recv_sync_file(self, uid):
        n = int(self.headers.get("Content-Length", -1))
        if not 0 <= n <= MAX_UPLOAD:
            return self.send_json({"error": "size"}, 413)
        tmp = FILES / f".part-sync-{secrets.token_hex(4)}"
        try:
            self.read_body_to(tmp, n)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return self.send_json({"ok": attach_file(uid, tmp)})

    def read_body_to(self, tmp, n):
        with tmp.open("wb") as f:
            left = n
            while left:
                chunk = self.rfile.read(min(left, 1024**2))
                if not chunk:
                    raise ConnectionError("上传中断")
                f.write(chunk)
                left -= len(chunk)

    def recv_file(self):
        n = int(self.headers.get("Content-Length", -1))
        if not 0 <= n <= MAX_UPLOAD:
            return self.send_json({"error": "size"}, 413)
        name = safe_name(urllib.parse.unquote(self.headers.get("X-Filename", "")))
        stored = f"{secrets.token_hex(4)}_{name}"
        tmp = FILES / f".part-{stored}"
        try:
            self.read_body_to(tmp, n)
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


def answer_discovery(s=None):
    """回应局域网广播 "LANCHAT?"，让另一台 app 自动找到本机。socket 被 stop() 关掉后退出。"""
    if s is None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", DISCOVERY_PORT))
    with s:
        while True:
            try:
                data, addr = s.recvfrom(64)
            except ConnectionResetError:  # Windows 把对方不可达报成这个，忽略即可
                continue
            except OSError:  # socket 已关闭
                return
            if data == b"LANCHAT?":
                # 旧版连接端只取第 2 段端口，且用 64 字节缓冲接收（Windows 上超长会整条丢弃），电脑名截短
                s.sendto(f"LANCHAT {PORT} {DEVICE_ID} {HOSTNAME}".encode()[:64], addr)


def start():
    """后台线程启动服务和广播应答，返回交给 stop() 的句柄；端口被占用时抛 OSError。"""
    server = Server(("0.0.0.0", PORT), Handler)
    disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        disc.bind(("", DISCOVERY_PORT))
    except OSError:
        server.server_close()
        disc.close()
        raise
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=answer_discovery, args=(disc,), daemon=True).start()
    return server, disc


def stop(handle):
    """关掉 start() 开的服务和广播应答，释放端口；之后别人再也连不到、搜不到本机。"""
    server, disc = handle
    server.shutdown()
    server.server_close()
    disc.close()


if __name__ == "__main__":
    try:
        server = Server(("0.0.0.0", PORT), Handler)
    except OSError as e:
        sys.exit(f"端口 {PORT} 无法使用（可能已经在运行）：{e}")
    threading.Thread(target=answer_discovery, daemon=True).start()
    print("LanChat 已启动，两台电脑的浏览器都打开下面任一地址：")
    for ip in lan_ips():
        print(f"    http://{ip}:{PORT}")
    print(f"本机也可用 http://localhost:{PORT}")
    print(f"访问码：{PIN}（每个浏览器只需输入一次）")
    print("关闭此窗口即停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
