#!/usr/bin/env python3
"""
roam_log.py - catat BSSID dan sinyal setiap detik selama uji roaming (jalan sepanjang koridor).
Hentikan dengan Ctrl+C. Jalankan ping di terminal lain (lihat modul Bagian 8.3).

  Linux  : python3 roam_log.py --iface wlan0 --out raw/roaming_L3.csv
  Windows: python roam_log.py --out raw\\roaming_L3.csv

Windows hanya memberi kualitas sinyal (%) tanpa scan; kolom rssi_dbm diisi aproksimasi
(persen/2 - 100) dan ditandai 'aprox' di kolom sumber.
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime

OS = os.environ.get("SURVEY_OS", sys.platform)  # "linux" / "win32" / "darwin"

from active_point import parse_link


def baca():
    if OS == "win32":
        import wlan_win
        bssid, q = wlan_win.link_quick()
        return bssid, (q / 2.0 - 100 if q is not None else ""), "aprox"
    if OS == "darwin":
        import wlan_mac
        bssid, rssi = wlan_mac.link_quick()
        return bssid, rssi, "corewlan"
    from scan_point import iface_otomatis
    out = subprocess.run(["iw", "dev", iface_otomatis(A.iface), "link"], capture_output=True, text=True).stdout
    l = parse_link(out)
    return l["bssid"], l["rssi"], "iw"


def main():
    global A
    p = argparse.ArgumentParser()
    p.add_argument("--iface", default="auto", help="interface Wi-Fi Linux (default: deteksi otomatis)")
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=1.0)
    A = p.parse_args()
    os.makedirs(os.path.dirname(A.out) or ".", exist_ok=True)
    prev = None
    with open(A.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["waktu", "bssid", "rssi_dbm", "sumber", "pindah"])
        try:
            while True:
                bssid, rssi, src = baca()
                pindah = "YA" if prev and bssid and bssid != prev else ""
                waktu = datetime.now().strftime("%H:%M:%S")
                w.writerow([waktu, bssid, rssi, src, pindah])
                fh.flush()
                print("%s %s %s dBm %s" % (waktu, bssid or "(tidak terhubung)", rssi,
                                           "<-- PINDAH AP" if pindah else ""))
                prev = bssid or prev
                time.sleep(A.interval)
        except KeyboardInterrupt:
            print("\nSelesai. Log tersimpan di", A.out)


if __name__ == "__main__":
    main()
