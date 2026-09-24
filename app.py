"""局域网传输桌面 app：首次在窗口里选"作为主机"或"连接另一台"，之后启动自动进入上次的模式。

主机端在后台跑 lanchat 服务并打开本机聊天页；连接端直接打开主机的聊天页。
"""
import json
import os
import shutil
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

import webview

import lanchat

VERSION = "1.0.2"
CONF = lanchat.DATA / "config.json"
RECEIVED = lanchat.DATA / "received"  # 连接端打开文件时下载到这里
# 双击即会执行的类型不直接打开，防止对方发来的程序被一点就运行
RISKY = {".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
         ".msp", ".scr", ".pif", ".lnk", ".url", ".hta", ".cpl", ".jar", ".reg", ".inf", ".appref-ms"}
SETUP = (lanchat.RES / "setup.html").read_text(encoding="utf-8")
LOCAL = f"127.0.0.1:{lanchat.PORT}"
# 局域网地址不该走系统代理（桌面环境可能注入失效的代理）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_conf():
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_conf(**kw):
    conf = load_conf()
    conf.update(kw)
    CONF.write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")


def normalize(addr):
    addr = addr.strip().removeprefix("http://").rstrip("/")
    return addr if ":" in addr else f"{addr}:{lanchat.PORT}"


def reachable(addr):
    """对方确实是 LanChat 服务才算连得上。"""
    try:
        with opener.open(f"http://{addr}/", timeout=2) as r:
            return b"lanchat_client" in r.read()
    except OSError:
        return False


def discover(timeout=1.5):
    own = set(lanchat.lan_ips())
    # 255.255.255.255 只从默认网卡发出，再按 /24 给每块网卡补一个定向广播
    targets = {"255.255.255.255"} | {ip.rsplit(".", 1)[0] + ".255" for ip in own}
    found = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        for target in targets:
            try:
                s.sendto(b"LANCHAT?", (target, lanchat.DISCOVERY_PORT))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = s.recvfrom(64)
            except OSError:  # 超时，或 Windows 把 ICMP 不可达报成 ConnectionResetError
                continue
            if data.startswith(b"LANCHAT ") and ip not in own:
                addr = f"{ip}:{data.split()[1].decode()}"
                if addr not in found:
                    found.append(addr)
    return found


class Api:
    """暴露给页面的 pywebview.api；下划线开头的成员不会暴露。"""

    def __init__(self):
        self._server = None
        self._error = ""

    def _start_host(self):
        if self._server is None:
            try:
                self._server = lanchat.start()
            except OSError:
                if not reachable(LOCAL):  # 被别的程序占了；若是另一个 LanChat 主机窗口就直接复用
                    return f"端口 {lanchat.PORT} 被其他程序占用"
        save_conf(mode="host")
        return ""

    def version(self):
        return VERSION

    def init(self):
        return {"last": load_conf().get("host", ""), "error": self._error}

    def find(self):
        return discover()

    def host(self):
        err = self._start_host()
        if err:
            return err
        window.load_url(f"http://{LOCAL}/")

    def join(self, addr):
        addr = normalize(addr or "")
        if addr.startswith(":"):
            return "请输入地址"
        if not reachable(addr):
            return f"连不上 {addr}：确认对方已作为主机启动，且防火墙已放行"
        save_conf(mode="join", host=addr)
        window.load_url(f"http://{addr}/")

    def open_file(self, mid, name):
        """用系统默认程序打开第 mid 条消息里的文件，成功返回空串，否则返回提示。"""
        mid = int(mid)
        if Path(name).suffix.lower() in RISKY:
            return "这是可执行文件，为安全起见不直接打开，请点“下载”后自行确认再运行"
        path = lanchat.stored_path(mid) if self._server else None
        if path is None:
            try:
                path = self._fetch(mid, name)
            except OSError as e:
                return f"打开失败：{e}"
        os.startfile(path)
        return ""

    def _fetch(self, mid, name):
        """从当前连接的主机下载文件到 received\\，已下载过的直接复用。"""
        base = urllib.parse.urlsplit(window.get_current_url() or "").netloc or LOCAL
        # 按主机分目录：换了主机后消息编号会重复
        path = RECEIVED / base.replace(":", "_") / f"{mid}_{lanchat.safe_name(name)}"
        if path.exists():
            return path
        pin = next((c["pin"].value for c in window.get_cookies() if "pin" in c), "")
        req = urllib.request.Request(f"http://{base}/files/{mid}", headers={"Cookie": f"pin={pin}"})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".part-" + path.name)
        try:
            with opener.open(req, timeout=10) as r, tmp.open("wb") as f:
                shutil.copyfileobj(r, f, 1024**2)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def reset(self):
        save_conf(mode="")
        self._error = ""
        window.load_html(SETUP)


api = Api()
conf = load_conf()
url = None
if conf.get("mode") == "host":
    api._error = api._start_host()
    url = None if api._error else f"http://{LOCAL}/"
elif conf.get("mode") == "join":
    if reachable(conf["host"]):
        url = f"http://{conf['host']}/"
    else:
        api._error = f"上次连接的主机 {conf['host']} 现在连不上，确认它已开启后重试"

window = webview.create_window("局域网传输", url=url, html=None if url else SETUP, js_api=api,
                               width=900, height=680, min_size=(480, 420))
webview.settings["ALLOW_DOWNLOADS"] = True
webview.start(private_mode=False, storage_path=str(lanchat.DATA / "webview"))
