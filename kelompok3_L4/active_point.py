#!/usr/bin/env python3
"""
active_point.py - uji aktif di satu titik: info link, ping (latency/jitter/loss),
dan (opsional) throughput iperf3. Laptop harus sudah terhubung ke SSID kampus.

Linux memakai `iw`; Windows memakai Native Wifi API (wlan_win.py); macOS memakai
WifiScanMac.app (wlan_mac.py). Tanpa --target, ping diarahkan ke gateway (deteksi otomatis).

Contoh:
  python3 active_point.py --iface wlan0 --titik L3-017 --target 10.34.0.1 \
      --out data/active_L3.csv [--iperf 10.34.0.10]
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime

OS = os.environ.get("SURVEY_OS", sys.platform)  # "linux" / "win32" / "darwin"

HEADER = ["timestamp", "titik_id", "bssid_assoc", "freq_mhz", "rssi_dbm", "tx_rate_mbps",
          "rtt_avg_ms", "rtt_max_ms", "jitter_ms", "loss_pct", "down_mbps", "up_mbps", "metode_rtt"]
TARGET_DEFAULT = "filkom.ub.ac.id"  # gateway UB tidak membalas ping (ICMP diblokir)


def jalankan(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=timeout).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def parse_link(text):
    g = lambda pat, cast=str: (lambda m: cast(m.group(1)) if m else "")(re.search(pat, text))
    return {"bssid": g(r"Connected to ([0-9a-f:]{17})"),
            "freq": g(r"freq:\s*([\d.]+)", lambda v: int(float(v))),
            "rssi": g(r"signal:\s*(-?\d+)", int),
            "tx": g(r"tx bitrate:\s*([\d.]+)", float)}


def parse_ping(text, sent):
    """Ambil RTT dari baris balasan (mengandung TTL=) - tahan terhadap bahasa Windows/Linux."""
    rtts = []
    for line in text.splitlines():
        if re.search(r"ttl=", line, re.I):
            m = re.search(r"[=<]\s*([\d.]+)\s*ms", line)
            if m:
                rtts.append(float(m.group(1)))
    return statistik(rtts, sent)


def statistik(rtts, sent):
    loss = round(100.0 * max(sent - len(rtts), 0) / sent, 1)
    if not rtts:
        return {"avg": "", "max": "", "jitter": "", "loss": loss}
    jitter = (sum(abs(b - a) for a, b in zip(rtts, rtts[1:])) / (len(rtts) - 1)) if len(rtts) > 1 else 0.0
    return {"avg": round(sum(rtts) / len(rtts), 2), "max": max(rtts),
            "jitter": round(jitter, 2), "loss": loss}


def parse_iperf(text):
    try:
        return round(json.loads(text)["end"]["sum_received"]["bits_per_second"] / 1e6, 1)
    except (ValueError, KeyError, TypeError):
        return ""


def tcp_rtt(host, n=20, port=443, jeda=0.2):
    """Latensi = waktu jabat tangan TCP. Dipakai bila ICMP (ping) diblokir."""
    import socket
    import time
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return statistik([], n)
    rtts = []
    for _ in range(n):
        t = time.perf_counter()
        try:
            socket.create_connection((ip, port), timeout=2).close()
            rtts.append(round((time.perf_counter() - t) * 1000, 2))
        except OSError:
            pass
        time.sleep(jeda)
    return statistik(rtts, n)


def gateway():
    """IP default gateway (Windows/macOS/Linux)."""
    if OS == "win32":
        out = jalankan(["powershell", "-NoProfile", "-Command",
                        "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric"
                        " | Select-Object -First 1).NextHop"])
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
    elif OS == "darwin":
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", jalankan(["route", "-n", "get", "default"]))
    else:
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", jalankan(["ip", "route", "show", "default"]))
    return m.group(1) if m else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iface", default="auto", help="interface Wi-Fi Linux (default: deteksi otomatis)")
    p.add_argument("--titik", required=True)
    p.add_argument("--target", default=TARGET_DEFAULT,
                   help="host/IP target latensi (default %s; 'gateway' = gateway otomatis)" % TARGET_DEFAULT)
    p.add_argument("--iperf", help="IP server iperf3 (opsional)")
    p.add_argument("--out", required=True)
    p.add_argument("--rawdir", default="raw/active")
    a = p.parse_args()
    os.makedirs(a.rawdir, exist_ok=True)

    if a.target == "gateway":
        a.target = gateway()
        if not a.target:
            p.error("gateway tidak terdeteksi (belum terhubung ke Wi-Fi?). Isi --target")
        print("Target ping = gateway %s" % a.target)
    if OS == "win32":
        import wlan_win
        link = wlan_win.link()
        n_ping = 20  # ping Windows tidak punya opsi interval (1 detik/paket)
        ping_txt = jalankan(["ping", "-n", str(n_ping), a.target])
    elif OS == "darwin":
        import wlan_mac
        link = wlan_mac.link()
        n_ping = 50
        ping_txt = jalankan(["ping", "-c", str(n_ping), "-i", "0.2", a.target])
    else:
        from scan_point import iface_otomatis
        link = parse_link(jalankan(["iw", "dev", iface_otomatis(a.iface), "link"]))
        n_ping = 50
        ping_txt = jalankan(["ping", "-c", str(n_ping), "-i", "0.2", a.target])
    open(os.path.join(a.rawdir, a.titik + "_ping.txt"), "w", encoding="utf-8").write(ping_txt)
    pg = parse_ping(ping_txt, n_ping)
    metode = "icmp"
    if pg["avg"] == "":  # tidak ada balasan ping sama sekali -> ukur dengan TCP
        print("Ping ke %s tidak dibalas (ICMP diblokir?) -> latensi diukur dengan TCP port 443" % a.target)
        pg, metode = tcp_rtt(a.target), "tcp443"
    down = up = ""
    if a.iperf:
        for arah, extra in (("down", ["-R"]), ("up", [])):
            js = jalankan(["iperf3", "-c", a.iperf, "-t", "8", "-J"] + extra)
            open(os.path.join(a.rawdir, "%s_%s.json" % (a.titik, arah)), "w", encoding="utf-8").write(js)
            if arah == "down":
                down = parse_iperf(js)
            else:
                up = parse_iperf(js)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(HEADER)
        w.writerow([datetime.now().isoformat(timespec="seconds"), a.titik, link["bssid"],
                    link["freq"], link["rssi"], link["tx"], pg["avg"], pg["max"],
                    pg["jitter"], pg["loss"], down, up, metode])
    print("[%s] BSSID %s RSSI %s dBm | RTT %s ms, jitter %s ms, loss %s%% (%s) | down %s / up %s Mbps"
          % (a.titik, link["bssid"], link["rssi"], pg["avg"], pg["jitter"], pg["loss"], metode, down, up))


if __name__ == "__main__":
    main()
