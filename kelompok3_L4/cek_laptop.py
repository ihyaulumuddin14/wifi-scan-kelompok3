#!/usr/bin/env python3
"""
cek_laptop.py - periksa apakah laptop siap untuk survei (Ubuntu/Linux, Windows, macOS).

  python cek_laptop.py [--ssid WiFi-UB.x] [--iface auto]

Memeriksa: versi Python, library, tkinter, perintah iw (Linux), interface & dukungan 5 GHz,
scan Wi-Fi sungguhan, latensi ke filkom.ub.ac.id, dan analyze.py pada hasil scan tadi.
Hasil akhir: daftar LULUS/GAGAL. Simpan keluarannya bila diminta asisten:
  python cek_laptop.py > cek_laptop.txt 2>&1
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile

HASIL = []
DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
OS = os.environ.get("SURVEY_OS", sys.platform)


def cek(nama, fungsi):
    try:
        info = fungsi()
        HASIL.append((nama, True, info or ""))
        print("[LULUS] %-28s %s" % (nama, info or ""))
    except BaseException as e:  # termasuk SystemExit dari skrip modul
        pesan = str(e) or e.__class__.__name__
        HASIL.append((nama, False, pesan))
        print("[GAGAL] %-28s %s" % (nama, pesan))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ssid", default="WiFi-UB.x")
    p.add_argument("--iface", default="auto")
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print("Sistem: %s %s, Python %s (%s)\n" % (platform.system(), platform.release(),
                                               platform.python_version(), sys.executable))

    def python():
        if sys.version_info < (3, 8):
            raise RuntimeError("butuh Python >= 3.8")
        return platform.python_version()
    cek("Python >= 3.8", python)

    def lib():
        import matplotlib
        import numpy
        import pandas
        import scipy
        return "pandas %s, numpy %s, scipy %s, matplotlib %s" % (
            pandas.__version__, numpy.__version__, scipy.__version__, matplotlib.__version__)
    cek("Library Python", lib)

    def tk():
        try:
            __import__("tkinter")
        except ImportError:
            raise RuntimeError("tkinter tidak ada -> jendela mark_points.py tidak bisa tampil. "
                               "Ubuntu: sudo apt install python3-tk (lalu buat ulang venv)")
        return "ok"
    cek("tkinter (mark_points.py)", tk)

    iface = None
    if OS.startswith("linux"):
        def iw():
            if not shutil.which("iw"):
                raise RuntimeError("iw tidak ada -> sudo apt install iw")
            return shutil.which("iw")
        cek("Perintah iw", iw)

        def interface():
            nonlocal iface
            from scan_point import iface_otomatis
            iface = iface_otomatis(a.iface)
            return iface
        cek("Interface Wi-Fi", interface)

        def lima_ghz():
            out = subprocess.run(["iw", "list"], stdout=subprocess.PIPE, text=True).stdout
            if "Band 2:" not in out or " 5180" not in out:
                raise RuntimeError("adaptor tidak mendukung 5 GHz")
            return "Band 2 (5 GHz) didukung"
        cek("Dukungan 5 GHz", lima_ghz)

        def sudo():
            if os.geteuid() == 0:
                return "PERINGATAN: dijalankan sebagai root; jalankan skrip TANPA sudo"
            print("        (password sudo mungkin diminta untuk `iw scan`)")
            r = subprocess.run(["sudo", "-v"])
            if r.returncode != 0:
                raise RuntimeError("sudo gagal; akun harus bisa sudo untuk `iw scan`")
            return "ok"
        cek("Hak sudo untuk iw scan", sudo)

    tmp = tempfile.mkdtemp(prefix="cek_wifi_")
    csv_path = os.path.join(tmp, "passive_cek.csv")

    def scan():
        args = [sys.executable, os.path.join(DIR, "scan_point.py"), "--titik", "CEK-001",
                "--lantai", "0", "--x", "100", "--y", "100", "--slot", "T0", "--device", "cek",
                "--out", csv_path, "--n", "1", "--ssid", a.ssid]
        if iface:
            args += ["--iface", iface]
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           errors="replace")
        print("        " + r.stdout.strip().replace("\n", "\n        "))
        if r.returncode != 0:
            raise RuntimeError("scan_point.py gagal (lihat pesan di atas)")
        import pandas as pd
        d = pd.read_csv(csv_path, dtype={"band": str})
        if d.empty:
            raise RuntimeError("tidak ada BSSID terdeteksi")
        if d.bssid.fillna("").eq("").all():
            raise RuntimeError("BSSID kosong (macOS: izinkan WifiScanMac di Location Services)")
        n5 = (d.band == "5").sum()
        if n5 == 0:
            raise RuntimeError("tidak ada BSSID 5 GHz")
        t = d[(d.ssid == a.ssid) & (d.band == "5")]
        if t.empty:
            raise RuntimeError("SSID '%s' 5 GHz tidak terlihat di sini" % a.ssid)
        return "%d BSSID (5 GHz: %d), %s terkuat %.0f dBm ch%s" % (
            len(d), n5, a.ssid, t.rssi_dbm.max(), int(t.loc[t.rssi_dbm.idxmax(), "channel"]))
    cek("Scan Wi-Fi (scan_point.py)", scan)

    def aktif():
        out = os.path.join(tmp, "active_cek.csv")
        args = [sys.executable, os.path.join(DIR, "active_point.py"), "--titik", "CEK-001",
                "--out", out, "--rawdir", os.path.join(tmp, "raw")]
        if iface:
            args += ["--iface", iface]
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           errors="replace")
        print("        " + r.stdout.strip().replace("\n", "\n        "))
        if r.returncode != 0:
            raise RuntimeError("active_point.py gagal")
        import pandas as pd
        row = pd.read_csv(out).iloc[0]
        if str(row.get("bssid_assoc", "")) in ("", "nan"):
            raise RuntimeError("laptop belum terhubung ke Wi-Fi (%s)" % a.ssid)
        if row.loss_pct >= 100:
            raise RuntimeError("filkom.ub.ac.id tidak terjangkau (ping dan TCP gagal)")
        return "RTT %s ms, loss %s%% (%s)" % (row.rtt_avg_ms, row.loss_pct, row.metode_rtt)
    cek("Uji aktif (active_point.py)", aktif)

    def analisis():
        if not os.path.exists(csv_path):
            raise RuntimeError("dilewati (scan gagal)")
        r = subprocess.run([sys.executable, os.path.join(DIR, "analyze.py"), "--passive", csv_path,
                            "--ssid", a.ssid, "--scale", "20", "--out", os.path.join(tmp, "out", "CEK")],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        if r.returncode != 0:
            raise RuntimeError(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "gagal")
        png = [f for f in os.listdir(os.path.join(tmp, "out")) if f.endswith(".png")]
        return "%d gambar dibuat" % len(png)
    cek("Analisis (analyze.py)", analisis)

    gagal = [h for h in HASIL if not h[1]]
    print("\n" + ("SEMUA LULUS - laptop siap survei." if not gagal else
                  "%d pemeriksaan GAGAL: %s" % (len(gagal), ", ".join(h[0] for h in gagal))))
    print("File uji sementara: %s" % tmp)
    sys.exit(1 if gagal else 0)


if __name__ == "__main__":
    main()
