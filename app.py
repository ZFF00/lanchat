"""局域网传输桌面 app：首次在窗口里选"作为主机"或"连接另一台"，之后启动自动进入上次的模式。

主机端在后台跑 lanchat 服务并打开本机聊天页；连接端直接打开主机的聊天页。
"""
import ctypes
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

import webview

import lanchat

VERSION = "1.0.6"
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


def copy_to_clipboard(text):
    """局域网 http 页面拿不到剪贴板权限，由 Python 直接调 Win32 剪贴板 API 写入 Unicode 文本。"""
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.argtypes, k32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
    k32.GlobalUnlock.argtypes = k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes, u32.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    data = (text + "\0").encode("utf-16-le")
    for _ in range(20):  # 剪贴板可能正被别的程序占用，稍等重试
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("剪贴板被其他程序占用")
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        ctypes.memmove(k32.GlobalLock(h), data, len(data))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(13, h):  # CF_UNICODETEXT；成功后内存归系统所有
            k32.GlobalFree(h)
            raise OSError("写入剪贴板失败")
    finally:
        u32.CloseClipboard()

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


def device_info(addr):
    """主机的设备编号和电脑名；v1.0.5 及更早的主机没有这个接口，返回 None。"""
    try:
        with opener.open(f"http://{addr}/api/device", timeout=2) as r:
            info = json.loads(r.read())
        return info if info.get("id") else None
    except (OSError, ValueError):
        return None


def discover(timeout=1.5):
    """广播搜索主机，返回 [{"addr", "id", "hostname"}]；旧版主机的应答里没有编号和电脑名。"""
    own = set(lanchat.lan_ips())
    # 255.255.255.255 只从默认网卡发出，再按 /24 给每块网卡补一个定向广播
    targets = {"255.255.255.255"} | {ip.rsplit(".", 1)[0] + ".255" for ip in own}
    found = {}
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
                data, (ip, _) = s.recvfrom(256)
            except OSError:  # 超时，或 Windows 把 ICMP 不可达报成 ConnectionResetError
                continue
            if data.startswith(b"LANCHAT ") and ip not in own:
                parts = data.decode(errors="replace").split() + ["", ""]
                addr = f"{ip}:{parts[1]}"
                found.setdefault(addr, {"addr": addr, "id": parts[2], "hostname": parts[3]})
    return list(found.values())


def favorites():
    return load_conf().get("favorites", [])


def remember(addr):
    """连上一台主机后记进常用设备：同一编号只更新 IP 和电脑名，自定义名字保持不变。"""
    info = device_info(addr) or {}
    # 旧版主机拿不到编号，暂用地址当编号；它升级后换成真编号，原来起的名字沿用
    dev_id = info.get("id") or f"addr:{addr}"
    favs = favorites()
    placeholder = next((f for f in favs if f["id"] == f"addr:{addr}" != dev_id), None)
    if placeholder:
        favs.remove(placeholder)
    fav = next((f for f in favs if f["id"] == dev_id), None)
    if fav is None:
        name = placeholder["name"] if placeholder else info.get("hostname") or addr
        fav = {"id": dev_id, "hostname": info.get("hostname", ""), "name": name}
        favs.append(fav)
    fav["addr"] = addr
    if info.get("hostname"):
        fav["hostname"] = info["hostname"]
    save_conf(favorites=favs)
    return dev_id


def locate(fav):
    """找到常用设备当前的地址：先试上次的 IP，不对再广播按编号找。找不到返回 None。"""
    if fav["id"].startswith("addr:"):
        return fav["addr"] if reachable(fav["addr"]) else None
    info = device_info(fav["addr"])
    if info and info["id"] == fav["id"]:
        return fav["addr"]
    # IP 变了，或者原 IP 现在被另一台主机占着
    return next((h["addr"] for h in discover() if h["id"] == fav["id"]), None)


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
        conf = load_conf()
        return {"last": conf.get("host", ""), "error": self._error, "favorites": conf.get("favorites", [])}

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
        save_conf(mode="join", host=addr, host_id=remember(addr))
        window.load_url(f"http://{addr}/")

    def open_favorite(self, dev_id):
        fav = next((f for f in favorites() if f["id"] == dev_id), None)
        if fav is None:
            return "这台设备已不在常用列表里"
        addr = locate(fav)
        if addr is None:
            return (f"找不到「{fav['name']}」（上次地址 {fav['addr']}）：确认对方已作为主机启动；"
                    "如果它换了 IP 又不在同一网段，请在下面手动输入新地址，连上后会自动更新这一条")
        return self.join(addr)

    def rename_favorite(self, dev_id, name):
        favs = favorites()
        for f in favs:
            if f["id"] == dev_id:
                f["name"] = name.strip() or f["hostname"] or f["addr"]  # 清空即恢复成电脑名
        save_conf(favorites=favs)
        return favs

    def remove_favorite(self, dev_id):
        favs = [f for f in favorites() if f["id"] != dev_id]
        save_conf(favorites=favs)
        return favs

    def open_file(self, mid, name):
        """兼容 v1.0.2 页面：等同 file_action(..., "open")，返回提示文本。"""
        return self.file_action(mid, name, "open")["error"]

    def file_action(self, mid, name, action):
        """对第 mid 条消息里的文件执行操作，返回 {"error": 提示, "info": 成功提示}。

        action: open 打开 / folder 打开所在目录 / saveas 另存为 / path 复制路径 / dir 复制目录路径
        连接端先把文件下载到本机 received\\，之后的路径、目录都指向这份本机副本。
        """
        if action == "open" and Path(name).suffix.lower() in RISKY:
            return {"error": "这是可执行文件，为安全起见不直接打开，请用“所在目录”找到它，确认安全后再运行", "info": ""}
        try:
            path = self._local_path(int(mid), name)
            if action == "open":
                os.startfile(path)
            elif action == "folder":
                subprocess.Popen(f'explorer /select,"{path}"')
            elif action == "saveas":
                picked = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
                if not picked:
                    return {"error": "", "info": ""}
                dest = picked if isinstance(picked, str) else picked[0]
                shutil.copyfile(path, dest)
                return {"error": "", "info": f"已保存到 {dest}"}
            elif action in ("path", "dir"):
                copy_to_clipboard(str(path if action == "path" else path.parent))
                return {"error": "", "info": "已复制"}
            else:
                return {"error": f"未知操作 {action}", "info": ""}
        except OSError as e:
            return {"error": f"操作失败：{e}", "info": ""}
        return {"error": "", "info": ""}

    def _local_path(self, mid, name):
        """文件在本机的位置：主机直接用收件目录里的原文件，连接端下载一份。"""
        path = lanchat.stored_path(mid) if self._server else None
        return path or self._fetch(mid, name)

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
    # 上次的主机若在常用设备里，IP 变了也能按编号找回来
    last = next((f for f in conf.get("favorites", []) if f["id"] == conf.get("host_id")), None)
    addr = locate(last) if last else (conf["host"] if reachable(conf["host"]) else None)
    if addr:
        if addr != conf["host"]:
            save_conf(host=addr, host_id=remember(addr))
        url = f"http://{addr}/"
    else:
        api._error = f"上次连接的主机 {conf['host']} 现在连不上，确认它已开启后重试"

window = webview.create_window("LanChat", url=url, html=None if url else SETUP, js_api=api,
                               width=900, height=680, min_size=(480, 420))
webview.settings["ALLOW_DOWNLOADS"] = True
# 打包后 pywebview 会取 exe 自带的图标；脚本运行时 exe 是 python.exe，所以显式指定
webview.start(private_mode=False, storage_path=str(lanchat.DATA / "webview"), icon=str(lanchat.RES / "icon.ico"))
