#!/usr/bin/env python3
"""
scan_point.py - passive scan Wi-Fi di satu titik ukur.
Linux: memakai `iw` (jalankan dengan sudo). Windows: memakai Native Wifi API (wlan_win.py),
tanpa admin. macOS: memakai WifiScanMac.app (wlan_mac.py). Di Windows/macOS --iface diabaikan.

Contoh (Linux):
  python3 scan_point.py --iface wlan0 --titik L3-017 --titik-csv data/titik_L3.csv \
       --slot T2 --device laptop-k2 --out data/passive_L3.csv
Contoh (Windows, Command Prompt/PowerShell):
  python scan_point.py --titik L3-017 --titik-csv data\\titik_L3.csv --slot T2 \
       --device laptop-k2 --out data\\passive_L3.csv
  (koordinat & lantai diambil dari titik_L3.csv; atau isi manual --lantai --x --y;
   atau kosongkan -> koordinat disambungkan saat analisis dengan analyze.py --titik)

Uji tanpa hardware (membaca output `iw scan` yang disimpan ke file):
  python3 scan_point.py --from-file contoh_iw_scan.txt ... --n 1
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time
from datetime import datetime

OS = os.environ.get("SURVEY_OS", sys.platform)  # "linux" / "win32" / "darwin"

HEADER = ["timestamp", "lantai", "titik_id", "x_px", "y_px", "slot_waktu", "device",
          "scan_ke", "ssid", "bssid", "freq_mhz", "channel", "band", "width_mhz",
          "rssi_dbm", "noise_dbm", "sta_count", "ch_util_pct", "security", "pmf",
          "catatan"]


def freq_to_channel(f):
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if f == 2484:
        return 14
    if 5000 < f < 5925:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    return None


def band_of(f):
    return "2.4" if f < 3000 else ("5" if f < 5925 else "6")


def _search(pattern, text, cast=str, flags=re.M):
    m = re.search(pattern, text, flags)
    return cast(m.group(1)) if m else None


def ssid_asli(teks):
    """`iw` menulis byte non-cetak/non-ASCII di SSID sebagai \\xNN (mis. 'Michelle\\x20',
    '\\xe9\\x92\\xb1'). Kembalikan ke teks UTF-8 aslinya; byte NUL (SSID tersembunyi) dibuang."""
    if "\\x" not in teks and "\\\\" not in teks:
        return teks
    out, i = bytearray(), 0
    while i < len(teks):
        if teks.startswith("\\x", i) and re.match(r"[0-9a-fA-F]{2}", teks[i + 2:i + 4]):
            out.append(int(teks[i + 2:i + 4], 16))
            i += 4
        elif teks.startswith("\\\\", i):
            out.append(0x5C)
            i += 2
        else:
            out += teks[i].encode("utf-8")
            i += 1
    return out.replace(b"\x00", b"").decode("utf-8", "replace")


def parse_block(block):
    ap = {"bssid": block[:17]}
    ap["freq"] = _search(r"^\s*freq:\s*([\d.]+)", block, lambda v: int(float(v)))
    ap["rssi"] = _search(r"^\s*signal:\s*(-?[\d.]+)\s*dBm", block, float)
    ap["ssid"] = ssid_asli(_search(r"^\s*SSID: ?(.*)$", block) or "")
    ap["sta_count"] = _search(r"station count:\s*(\d+)", block, int)
    util = _search(r"channel utili[sz]ation:\s*(\d+)/255", block, int)
    ap["ch_util_pct"] = round(util * 100 / 255, 1) if util is not None else None

    # Lebar kanal: HT (20/40) lalu VHT (80/160) bila ada
    width = 20
    offset = _search(r"secondary channel offset:\s*(\w+)", block)
    if offset in ("above", "below"):
        width = 40
    vht = _search(r"VHT operation:\s*\n\s*\*\s*channel width:\s*(\d)", block, int)
    if vht == 1:
        width = 80
    elif vht in (2, 3):
        width = 160
    ap["width"] = width

    # Keamanan (dibaca dari beacon, pasif)
    if "RSN:" in block:
        akm = _search(r"RSN:.*?Authentication suites:\s*(.*)$", block, flags=re.S | re.M) or ""
        if "SAE" in akm and "PSK" in akm:
            sec = "WPA2/WPA3-Personal"
        elif "SAE" in akm:
            sec = "WPA3-Personal"
        elif "802.1X" in akm:
            sec = "WPA2/3-Enterprise"
        elif "OWE" in akm:
            sec = "OWE"
        elif "PSK" in akm:
            sec = "WPA2-Personal"
        else:
            sec = "RSN-other"
    elif "WPA:" in block:
        sec = "WPA(legacy)"
    elif re.search(r"capability:.*Privacy", block):
        sec = "WEP"
    else:
        sec = "Open"
    ap["security"] = sec
    ap["pmf"] = ("required" if "MFP-required" in block
                 else "capable" if "MFP-capable" in block else "no")
    return ap


def parse_iw_scan(text):
    blocks = re.split(r"^BSS (?=[0-9a-f]{2}(?::[0-9a-f]{2}){5})", text, flags=re.M)
    aps = [parse_block(b) for b in blocks if re.match(r"[0-9a-f]{2}:", b)]
    return [a for a in aps if a["freq"] and a["rssi"] is not None]


def iface_otomatis(iface="auto"):
    """Nama interface Wi-Fi Linux (Ubuntu memakai nama seperti wlp2s0, bukan wlan0)."""
    if iface and iface != "auto":
        return iface
    try:
        out = subprocess.run(["iw", "dev"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True).stdout
    except FileNotFoundError:
        raise SystemExit("perintah `iw` tidak ada. Pasang: sudo apt install iw")
    nama = re.findall(r"^\s*Interface\s+(\S+)", out, re.M)
    nama = [n for n in nama if not n.startswith("p2p")]
    if not nama:
        raise SystemExit("tidak ada interface Wi-Fi (cek: iw dev)")
    return nama[0]


def iw_scan(iface, coba=3):
    """`iw scan` butuh hak root: skrip dijalankan sebagai user biasa dan memanggil `sudo iw`
    (password diminta sekali), sehingga file data tetap milik user, bukan root."""
    cmd = ["iw", "dev", iface, "scan"]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    for k in range(coba):
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        if r.returncode == 0:
            return r.stdout
        pesan = r.stderr.strip()
        if "busy" in pesan.lower() and k < coba - 1:  # (-16) scan lain sedang berjalan
            time.sleep(2)
            continue
        petunjuk = {"No such device": "nama interface salah; cek dengan: iw dev",
                    "Network is down": "Wi-Fi mati; nyalakan: nmcli radio wifi on",
                    "Operation not permitted": "butuh hak root (sudo)"}
        tip = next((v for k2, v in petunjuk.items() if k2 in pesan), "")
        raise SystemExit("iw scan gagal: %s%s" % (pesan, (" -> " + tip) if tip else ""))
    raise SystemExit("iw scan gagal: perangkat sibuk")


def noise_by_freq(iface):
    """Noise floor per frekuensi dari `iw survey dump` (tergantung driver)."""
    try:
        out = subprocess.run(["iw", "dev", iface, "survey", "dump"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    res, freq = {}, None
    for line in out.splitlines():
        m = re.search(r"frequency:\s*(\d+)", line)
        if m:
            freq = int(m.group(1))
        m = re.search(r"noise:\s*(-?\d+)\s*dBm", line)
        if m and freq:
            res[freq] = int(m.group(1))
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iface", default="auto", help="interface Wi-Fi Linux (default: deteksi otomatis)")
    for k in ["titik", "slot", "device", "out"]:
        p.add_argument("--" + k, required=True)
    p.add_argument("--titik-csv", help="CSV titik ukur (titik_id,lantai,x_px,y_px,...)")
    p.add_argument("--lantai")
    p.add_argument("--x")
    p.add_argument("--y")
    p.add_argument("--n", type=int, default=3, help="jumlah scan per titik")
    p.add_argument("--catatan", default="")
    p.add_argument("--ssid", help="SSID kampus: tampilkan RSSI terkuatnya (5 GHz) untuk dicatat di sketsa")
    p.add_argument("--from-file", help="baca output iw scan dari file (untuk uji)")
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # konsol Windows: jangan crash karena emoji SSID
        sys.stdout.reconfigure(errors="replace")
    if a.titik_csv:
        with open(a.titik_csv, encoding="utf-8-sig") as fh:
            row = next((r for r in csv.DictReader(fh) if r["titik_id"] == a.titik), None)
        if row is None:
            p.error("titik %s tidak ada di %s" % (a.titik, a.titik_csv))
        a.lantai, a.x, a.y = row["lantai"], row["x_px"], row["y_px"]
    if None in (a.x, a.y):
        # boleh: koordinat disambungkan belakangan lewat analyze.py --titik
        a.x = a.x or ""
        a.y = a.y or ""
    a.lantai = a.lantai or ""

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(HEADER)
        for i in range(1, a.n + 1):
            ts = datetime.now().isoformat(timespec="seconds")
            if a.from_file:
                aps, noise = parse_iw_scan(open(a.from_file, encoding="utf-8").read()), {}
            elif OS == "win32":
                import wlan_win
                aps, noise = wlan_win.scan(), {}
            elif OS == "darwin":
                import wlan_mac
                aps, noise = wlan_mac.scan(), {}
            else:
                iface = iface_otomatis(a.iface)
                aps, noise = parse_iw_scan(iw_scan(iface)), noise_by_freq(iface)
            for ap in aps:
                w.writerow([ts, a.lantai, a.titik, a.x, a.y, a.slot, a.device, i,
                            ap["ssid"], ap["bssid"], ap["freq"], freq_to_channel(ap["freq"]),
                            band_of(ap["freq"]), ap["width"], ap["rssi"],
                            noise.get(ap["freq"], ap.get("noise") or ""), ap["sta_count"] if ap["sta_count"] is not None else "",
                            ap["ch_util_pct"] if ap["ch_util_pct"] is not None else "",
                            ap["security"], ap["pmf"], a.catatan])
            n5 = sum(1 for ap in aps if band_of(ap["freq"]) == "5")
            print("[%s] scan %d/%d: %d BSSID (5 GHz: %d)" % (a.titik, i, a.n, len(aps), n5))
            if a.ssid:
                t = [ap for ap in aps if ap["ssid"] == a.ssid and band_of(ap["freq"]) == "5"]
                if t:
                    b = max(t, key=lambda ap: ap["rssi"])
                    print("    %s 5 GHz terkuat: %.0f dBm (ch %s, %s)"
                          % (a.ssid, b["rssi"], freq_to_channel(b["freq"]), b["bssid"]))
                else:
                    print("    %s 5 GHz: TIDAK TERDETEKSI" % a.ssid)
            if i < a.n:
                time.sleep(5)


if __name__ == "__main__":
    main()
