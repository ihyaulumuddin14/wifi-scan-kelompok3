"""
wlan_mac.py - akses Wi-Fi di macOS. Dipakai otomatis oleh scan_point.py, active_point.py,
dan roam_log.py bila dijalankan di macOS.

macOS hanya memberi SSID/BSSID kepada aplikasi yang punya izin Location Services, sehingga
scan dilakukan oleh aplikasi pembantu WifiScanMac.app (di folder yang sama dengan skrip ini).

Persiapan (sekali):
  xcode-select --install                 # bila swiftc belum ada
  sh mac_helper/build_mac_helper.sh      # membuat WifiScanMac.app
  python scan_point.py --titik UJI ...   # pertama kali: klik "Allow" pada dialog izin lokasi
  (atau System Settings > Privacy & Security > Location Services > WifiScanMac: aktifkan)
"""
import base64
import json
import os
import subprocess
import tempfile

from wlan_win import parse_ies  # pengurai beacon (murni Python, tidak butuh Windows)

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "WifiScanMac.app")
LEBAR = {1: 20, 2: 40, 3: 80, 4: 160}  # CWChannelWidth


def _freq(ch, band):
    if band == 1:
        return 2484 if ch == 14 else 2407 + 5 * ch
    if band == 3:
        return 5950 + 5 * ch
    return 5000 + 5 * ch


def _keamanan(flags):
    """flags = daftar nilai CWSecurity yang didukung. Catatan: macOS juga menandai
    WPA2-Personal sebagai WPA3Transition (13), jadi nilai itu diabaikan."""
    f = set(flags)
    if f & {7, 8, 9, 10, 12}:
        return "WPA2/3-Enterprise"
    if 11 in f:
        return "WPA2/WPA3-Personal" if 4 in f else "WPA3-Personal"
    if f & {2, 3, 4, 5}:
        return "WPA2-Personal"
    if f & {14, 15}:
        return "OWE"
    if f & {1, 6}:
        return "WEP"
    return "Open" if 0 in f else ""


def _jalankan(hanya_link=False, batas=320):
    if not os.path.isdir(APP):
        raise SystemExit("WifiScanMac.app belum ada. Jalankan dulu: sh mac_helper/build_mac_helper.sh")
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(out)
    cmd = ["open", "-W", "-n", APP, "--args", out] + (["--link"] if hanya_link else [])
    subprocess.run(cmd, timeout=batas, check=False)
    if not os.path.exists(out):
        raise SystemExit("WifiScanMac tidak menghasilkan data (dialog izin lokasi belum dijawab?)")
    with open(out, encoding="utf-8") as fh:
        d = json.load(fh)
    os.remove(out)
    if "error" in d:
        raise SystemExit("WifiScanMac: " + d["error"])
    return d


def scan():
    aps = []
    for n in _jalankan().get("networks", []):
        ie = base64.b64decode(n["ie"]) if n.get("ie") else b""
        p = parse_ies(ie, 0x0011) if ie else {}
        aps.append({"bssid": n["bssid"].lower(), "ssid": n["ssid"],
                    "freq": _freq(n["channel"], n["band"]), "rssi": float(n["rssi"]),
                    "noise": n.get("noise"),
                    "width": p.get("width") if ie else LEBAR.get(n["width"], 20),
                    "sta_count": p.get("sta_count"), "ch_util_pct": p.get("ch_util_pct"),
                    "security": _keamanan(n.get("security", [])), "pmf": p.get("pmf", "")})
    return aps


def link():
    l = _jalankan(hanya_link=True)["link"]
    return {"bssid": l["bssid"].lower(), "freq": _freq(l["channel"], l["band"]) if l["channel"] else "",
            "rssi": l["rssi"], "tx": l["tx"] or ""}


def link_quick():
    l = _jalankan(hanya_link=True)["link"]
    return l["bssid"].lower(), l["rssi"]
