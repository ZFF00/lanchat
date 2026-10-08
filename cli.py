"""LanChat 命令行工具：查看连接状态、发文本/文件、实时看消息、拉取文件。

    lanchat-cli status                 本机模式、连着谁、谁连着本机、常用设备是否在线、最近同步
    lanchat-cli send 你好               发文本给当前连着的主机（本机是主机时发到本机）
    lanchat-cli send 报告.pdf 截图.png   发文件（参数是已存在的文件就当文件发）
    lanchat-cli send -t 笔记本HP 你好    -t 指定对象：常用设备名、电脑名、IP[:端口]，或 本机
    lanchat-cli watch                  持续显示新消息；连接断开、恢复时会提示
    lanchat-cli pull -o 收件            把聊天里的文件都下载到目录（已下载过的跳过）
    lanchat-cli login -t 笔记本HP        输入访问码（之后自动记住）

与桌面 app 共用 %APPDATA%\\LanChat 里的配置、常用设备和访问码；不需要 app 开着也能用。
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from common import (DEVICE_FILE, PORT, SYNC_STATE, device_info, discover, favorites, hosting_at, identity_headers,
                    load_conf, load_pin, locate, normalize, opener, read_json, read_text, save_pin)

LOCAL = f"127.0.0.1:{PORT}"


class CliError(Exception):
    pass


def interactive():
    """能不能在终端里问用户。Windows 上输入重定向自 NUL 时 isatty() 也返回 True，要再确认它真是控制台。"""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt
    mode = ctypes.c_uint()
    return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)))


# ---------- 连接对象 ----------

class Target:
    def __init__(self, addr, label, key):
        self.addr, self.label, self.key = addr, label, key  # key：存访问码用的设备编号或地址
        self.pin = "" if addr == LOCAL else load_pin(key)

    def request(self, path, data=None, headers=None, method=None, timeout=10):
        """带访问码和本机身份发请求；访问码不对时在终端里问一次。"""
        for attempt in (1, 2):
            hdr = identity_headers() | (headers or {})
            if self.pin:
                hdr["Cookie"] = f"pin={self.pin}"
            req = urllib.request.Request(f"http://{self.addr}{path}", data=data, headers=hdr, method=method)
            try:
                return opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                if e.code != 401 or attempt == 2:
                    raise
                self.login()
                if hasattr(data, "seek"):
                    data.seek(0)

    def json(self, path, **kw):
        with self.request(path, **kw) as r:
            return json.loads(r.read())

    def login(self, pin=None):
        if pin is None:
            if not interactive():
                raise CliError(f"{self.label} 需要访问码：先运行 lanchat-cli login -t {self.label}")
            pin = getpass.getpass(f"{self.label} 的访问码（显示在主机窗口顶部）：").strip()
        body = json.dumps({"pin": pin}).encode()
        req = urllib.request.Request(f"http://{self.addr}/api/login", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            opener.open(req, timeout=10).close()
        except urllib.error.HTTPError as e:
            raise CliError("访问码不对" if e.code == 403 else f"登录失败：{e}")
        self.pin = pin
        save_pin(self.key, pin)


def resolve(name):
    """把 -t 参数（或默认）解析成一台正在当主机的设备。"""
    conf = load_conf()
    if name is None:
        if conf.get("mode") == "join" and conf.get("host"):
            fav = next((f for f in favorites() if f["id"] == conf.get("host_id")), None)
            return from_favorite(fav) if fav else from_addr(conf["host"])
        if conf.get("mode") == "host":
            return Target(LOCAL, "本机", "local")
        raise CliError("本机的 LanChat 既没当主机也没连别人，请用 -t 指定对象，例如 -t 192.168.3.136")
    if name.lower() in ("本机", "local", "localhost"):
        return Target(LOCAL, "本机", "local")
    low = name.lower()
    fav = next((f for f in favorites() if low in (f["name"].lower(), f["hostname"].lower(), f["id"])), None)
    return from_favorite(fav) if fav else from_addr(normalize(name))


def from_favorite(fav):
    addr = locate(fav)
    if addr is None:
        raise CliError(f"「{fav['name']}」当前没有作为主机运行（上次地址 {fav['addr']}）")
    return Target(addr, fav["name"], fav["id"] if not fav["id"].startswith("addr:") else addr)


def from_addr(addr):
    info = device_info(addr)
    if info is None:
        raise CliError(f"{addr} 上没有 LanChat 主机在运行")
    return Target(addr, info.get("hostname") or addr, info["id"])


# ---------- 显示 ----------

def ago(sec):
    if sec is None:
        return "从未"
    sec = max(0, sec)
    return ("刚刚" if sec < 10 else f"{int(sec)} 秒前" if sec < 60 else f"{int(sec // 60)} 分钟前" if sec < 3600
            else f"{int(sec // 3600)} 小时前")


def duration(sec):
    return "不到 1 分钟" if sec < 60 else f"{int(sec // 60)} 分钟" if sec < 3600 else f"{sec / 3600:.1f} 小时"


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def my_clients():
    return set(load_conf().get("clients", [])) | {cli_client()}


def cli_client():
    return f"cli:{read_text(DEVICE_FILE) or 'unknown'}"


def show_message(m, mine):
    t = datetime.fromtimestamp(m["ts"]).strftime("%m-%d %H:%M")
    who = "我" if m.get("client") in mine else f"对方 {m.get('ip', '')}"
    if m["type"] == "text":
        body = m["text"].replace("\n", "\n    ")
    else:
        body = f"[文件] {m['name']} ({fmt_size(m.get('size', 0))})" + ("  同步中…" if m.get("has") is False else "")
    print(f"[{t}] {who}：{body}", flush=True)


# ---------- 命令 ----------

def cmd_status(args):
    conf = load_conf()
    mode = conf.get("mode", "")
    me = read_text(DEVICE_FILE)
    out = {"device": me, "mode": mode or "none"}
    exit_code = 0

    favs = favorites()
    with ThreadPoolExecutor(max_workers=len(favs) + 2) as pool:
        hosts_f = pool.submit(discover, names=False)
        local_f = pool.submit(device_info, LOCAL, 1)
        probes = {f["id"]: pool.submit(hosting_at, f) for f in favs}
        hosts = hosts_f.result()
        local = local_f.result()
    seen_ids, seen_addrs = {h["id"] for h in hosts if h["id"]}, {h["addr"] for h in hosts}
    online = {fid: p.result() or fid in seen_ids or fid.removeprefix("addr:") in seen_addrs
              for fid, p in probes.items()}

    if mode == "host" or local:
        out["host_running"] = bool(local)
        if local:
            st = Target(LOCAL, "本机", "local").json("/api/status")
            out["peers"] = st["peers"]
            out["now"] = st["now"]
            out["messages"] = st["messages"]
        elif mode == "host":
            exit_code = 1
    if mode == "join":
        st = read_json(SYNC_STATE, {})
        fav = next((f for f in favs if f["id"] == conf.get("host_id")), None)
        up = online.get(fav["id"]) if fav else device_info(conf.get("host", "")) is not None
        out["host"] = {"addr": conf.get("host"), "name": fav["name"] if fav else conf.get("host"), "online": up}
        out["sync"] = st
        exit_code = 0 if up else 1
    out["favorites"] = [dict(f, online=online[f["id"]]) for f in favs]

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return exit_code

    now = time.time()
    mode_text = {"host": "主机", "join": "连接端"}.get(mode, "未选择（启动页）")
    print(f"本机模式：{mode_text}")
    if "host_running" in out:
        if out["host_running"]:
            peers = out["peers"]
            print(f"主机服务：运行中，共 {out['messages']} 条消息")
            print("连着本机的设备：" + ("" if peers else "无"))
            names = {f["id"]: f["name"] for f in favs}
            for p in peers:
                name = names.get(p["device"]) or p["hostname"] or p["ip"]
                dot = "●" if p["online"] else "○"
                state = (f"在线 · 已连 {duration(out['now'] - p['since'])}" if p["online"]
                         else f"已断开 · {ago(out['now'] - p['last_seen'])}")
                sync = f"最近同步 {ago(out['now'] - p['last_sync'])}" if p["last_sync"] else "未同步过"
                print(f"  {dot} {name:<16} {p['ip']:<15} {state}   {sync}")
        else:
            print("主机服务：没有运行（LanChat 没开？）")
    if mode == "join":
        h, st = out["host"], out["sync"]
        print(f"连着：{h['name']}（{h['addr']}）  {'● 主机在线' if h['online'] else '○ 主机连不上'}")
        if st.get("last_ok"):
            line = f"最近同步：{ago(now - st['last_ok'])}成功"
            if st.get("pending_files"):
                line += f"，还有 {st['pending_files']} 个文件在传"
            print(line)
        if st.get("error") and st.get("last_try", 0) > st.get("last_ok", 0):
            print(f"同步失败（{ago(now - st['last_try'])}）：{st['error']}")
        if not st:
            print("最近同步：还没有记录（app 开着才会同步）")
    if favs:
        print("常用设备：")
        for f in out["favorites"]:
            dot, state = ("●", "正在当主机") if f["online"] else ("○", "未作为主机")
            host = f" ({f['hostname']})" if f["hostname"] and f["hostname"] != f["name"] else ""
            print(f"  {dot} {f['name']}{host:<20} {f['addr']:<21} {state}")
    return exit_code


def cmd_send(args):
    t = resolve(args.to)
    items = [sys.stdin.read() if a == "-" else a for a in args.items]
    if args.text:
        items = [" ".join(items)]
    for item in items:
        p = Path(item)
        if not args.text and len(item) < 260 and p.is_file():
            size = p.stat().st_size
            with p.open("rb") as f:
                t.request("/api/file", data=f, method="POST", timeout=600,
                          headers={"X-Filename": urllib.parse.quote(p.name), "X-Client": cli_client(),
                                   "Content-Length": str(size), "Content-Type": "application/octet-stream"}).close()
            print(f"已发送文件给 {t.label}：{p.name}（{fmt_size(size)}）")
        else:
            body = json.dumps({"text": item, "client": cli_client()}).encode()
            t.request("/api/text", data=body, method="POST", headers={"Content-Type": "application/json"}).close()
            print(f"已发送给 {t.label}：{item if len(item) <= 40 else item[:40] + '…'}")
    return 0


def cmd_watch(args):
    t = resolve(args.to)
    mine, printed, last, down = my_clients(), set(), 0, False
    first = t.json("/api/messages?since=0")
    for m in first["messages"][-args.n:] if args.n else []:
        show_message(m, mine)
    printed = {m.get("uid") or m["id"] for m in first["messages"]}
    last, rev = (first["messages"][-1]["id"] if first["messages"] else 0), first.get("rev")
    print(f"—— 正在接收 {t.label}（{t.addr}）的新消息，Ctrl+C 退出 ——", flush=True)
    while True:
        time.sleep(1)
        try:
            data = t.json(f"/api/messages?since={last}", timeout=5)
            if data.get("rev") != rev:  # 同步插入了更早的消息，编号变了：整份重取，只打印没打印过的
                data, rev = t.json("/api/messages?since=0", timeout=5), data.get("rev")
            if down:
                print(f"[恢复] 已重新连上 {t.label}", flush=True)
                down = False
            for m in data["messages"]:
                last = max(last, m["id"])
                key = m.get("uid") or m["id"]
                if key not in printed:
                    printed.add(key)
                    show_message(m, mine)
        except (OSError, ValueError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 401:
                raise CliError(f"{t.label} 的访问码变了，请重新 login")
            if not down:
                print(f"[断开] 与 {t.label} 的连接已断开，重试中…（{e.__class__.__name__}）", flush=True)
                down = True


def cmd_pull(args):
    t = resolve(args.to)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    msgs = [m for m in t.json("/api/messages?since=0")["messages"] if m["type"] == "file"]
    got = skipped = waiting = 0
    for m in msgs:
        if m.get("has") is False:
            waiting += 1
            continue
        dest = out / m["name"]
        if dest.exists() and dest.stat().st_size == m["size"]:
            skipped += 1
            continue
        n = 1
        while dest.exists():  # 同名不同内容，改名保存
            dest = out / f"{Path(m['name']).stem} ({n}){Path(m['name']).suffix}"
            n += 1
        tmp = dest.with_name(dest.name + ".part")
        with t.request(f"/files/{m['id']}", timeout=600) as r, tmp.open("wb") as f:
            while chunk := r.read(1024**2):
                f.write(chunk)
        tmp.replace(dest)
        got += 1
        print(f"已下载：{dest.name}（{fmt_size(m['size'])}）", flush=True)
    print(f"完成：新下载 {got} 个，已存在跳过 {skipped} 个" + (f"，{waiting} 个还在同步中" if waiting else ""))
    return 0


def cmd_login(args):
    t = resolve(args.to)
    if t.addr == LOCAL:
        print("本机访问不需要访问码")
        return 0
    t.login(args.pin)
    print(f"已登录 {t.label}，访问码已记住")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="lanchat-cli", description="LanChat 命令行工具",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("-t", "--to", help="常用设备名、电脑名、IP[:端口] 或 本机；不填用当前连着的主机")

    p = sub.add_parser("status", help="连接状态")
    p.add_argument("--json", action="store_true", help="输出 JSON，便于脚本使用")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("send", parents=[target], help="发文本或文件")
    p.add_argument("items", nargs="+", help="文本，或文件路径；- 表示从标准输入读文本")
    p.add_argument("--text", action="store_true", help="全部当作一条文本发送，即使和文件同名")
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser("watch", parents=[target], help="持续显示新消息")
    p.add_argument("-n", type=int, default=10, help="先显示最近几条（默认 10）")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("pull", parents=[target], help="下载聊天里的所有文件")
    p.add_argument("-o", "--out", default=".", help="保存到的目录（默认当前目录）")
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("login", parents=[target], help="输入并记住访问码")
    p.add_argument("--pin", help="直接给出访问码（不填则在终端里输入）")
    p.set_defaults(fn=cmd_login)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except CliError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except urllib.error.HTTPError as e:
        print(f"错误：主机返回 {e.code}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"错误：连接失败：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
