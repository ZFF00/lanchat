"""主机服务、桌面 app、命令行工具共用的路径和局域网工具。只用标准库，导入时不做任何读写。"""
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("LANCHAT_PORT", 8765))
DISCOVERY_PORT = PORT + 1
BASE = Path(__file__).resolve().parent
# exe 所在目录可能随手放在桌面/下载，数据统一放 %APPDATA%\LanChat；脚本运行时放在脚本旁；测试时用 LANCHAT_DATA 指定
DATA = Path(os.environ.get("LANCHAT_DATA") or
            (Path(os.environ["APPDATA"]) / "LanChat" if getattr(sys, "frozen", False) else BASE / "data"))
CONF = DATA / "config.json"
SYNC_STATE = DATA / "sync_state.json"  # 连接端最近一次同步的结果，供命令行 status 读取
DEVICE_FILE = DATA / "device_id.txt"
ONLINE_SECS = 10  # 主机这么久没收到某台的请求，就认为它已断开（页面每秒轮询一次）
# 局域网地址不该走系统代理（桌面环境可能注入失效的代理）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    """先写临时文件再替换，另一个进程（命令行工具）读到的不会是写了一半的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_conf():
    return read_json(CONF, {})


def save_conf(**kw):
    conf = load_conf()
    conf.update(kw)
    write_json(CONF, conf)


def favorites():
    return load_conf().get("favorites", [])


# 各主机的访问码，按设备编号（旧版主机按地址）存；app 同步成功后写入，命令行登录时也写入
PINS = DATA / "pins.json"


def load_pin(key):
    return read_json(PINS, {}).get(key, "")


def save_pin(key, pin):
    pins = read_json(PINS, {})
    if pin and pins.get(key) != pin:
        pins[key] = pin
        write_json(PINS, pins)


def identity_headers():
    """告诉主机"我是哪台"，主机的连接状态里就能显示电脑名而不只是 IP。"""
    dev = read_text(DEVICE_FILE)
    return {"X-Device": dev, "X-Hostname": urllib.parse.quote(socket.gethostname())} if dev else {}


def read_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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


def normalize(addr):
    addr = addr.strip().removeprefix("http://").rstrip("/")
    return addr if ":" in addr else f"{addr}:{PORT}"


def reachable(addr):
    """对方确实是 LanChat 服务才算连得上。"""
    try:
        with opener.open(f"http://{addr}/", timeout=2) as r:
            return b"lanchat_client" in r.read()
    except OSError:
        return False


def device_info(addr, timeout=2):
    """主机的设备编号和电脑名；不是主机或是 v1.0.5 及更早的主机时返回 None。"""
    try:
        with opener.open(f"http://{addr}/api/device", timeout=timeout) as r:
            info = json.loads(r.read())
        return info if info.get("id") else None
    except (OSError, ValueError):
        return None


def reverse_name(addr, timeout=2):
    """旧版主机不报电脑名时，按 IP 向局域网反查（NetBIOS/LLMNR/DNS）；查不到返回空串。"""
    ip = addr.rsplit(":", 1)[0]
    result = []

    def lookup():
        try:
            result.append(socket.gethostbyaddr(ip)[0])
        except OSError:
            pass

    # gethostbyaddr 没有超时参数，放进线程里等，避免网络不通时卡住界面
    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    name = result[0].split(".")[0] if result else ""
    return "" if not name or name == ip.split(".")[0] else name.upper()


def discover(timeout=1.5, names=True):
    """广播搜索主机，返回 [{"addr", "id", "hostname"}]；旧版主机的应答里没有编号，电脑名靠反查（names=False 时跳过）。"""
    own = set(lan_ips())
    # 255.255.255.255 只从默认网卡发出，再按 /24 给每块网卡补一个定向广播
    targets = {"255.255.255.255"} | {ip.rsplit(".", 1)[0] + ".255" for ip in own}
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        for target in targets:
            try:
                s.sendto(b"LANCHAT?", (target, DISCOVERY_PORT))
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
    # 旧版主机并行反查电脑名
    old = [h for h in found.values() if not h["hostname"]] if names else []
    threads = [threading.Thread(target=lambda h=h: h.update(hostname=reverse_name(h["addr"])), daemon=True)
               for h in old]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2.5)
    return list(found.values())


def hosting_at(fav):
    """常用设备上次的地址上，现在是不是这台设备在当主机。"""
    if fav["id"].startswith("addr:"):  # 旧版主机没有编号，只能看地址上有没有 LanChat
        return reachable(fav["addr"])
    info = device_info(fav["addr"])
    return bool(info) and info["id"] == fav["id"]


def locate(fav, hosts=None):
    """找到常用设备当前的地址：先试上次的 IP，不对再广播按编号找。找不到返回 None。

    hosts: 已经广播搜到的结果，批量查多台时传入，避免每台都广播一次。
    """
    if fav["id"].startswith("addr:"):
        return fav["addr"] if reachable(fav["addr"]) else None
    if hosting_at(fav):
        return fav["addr"]
    # IP 变了，或者原 IP 现在被另一台主机占着
    return next((h["addr"] for h in (discover() if hosts is None else hosts) if h["id"] == fav["id"]), None)
