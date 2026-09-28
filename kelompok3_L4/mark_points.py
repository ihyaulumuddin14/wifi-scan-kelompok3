#!/usr/bin/env python3
"""
mark_points.py - (untuk asisten) kalibrasi skala denah dan penandaan titik ukur dengan klik.

  python3 mark_points.py --denah denah/L3.png --lantai 3 --out data/titik_L3.csv

Langkah:
 1. Klik 2 titik yang jaraknya diketahui (mis. ujung-ujung koridor), lalu isi jaraknya (m)
    di terminal -> skala (piksel/meter) dicetak dan disimpan ke data/skala_L3.txt.
 2. Klik titik-titik ukur secara berurutan. Klik kanan = hapus titik terakhir.
    Tekan Enter untuk selesai. ID dibuat otomatis: L3-001, L3-002, ...
 3. Gambar denah bertitik disimpan ke <out>.png untuk dicetak dan dibawa ke lapangan.
"""
import argparse
import csv
import math
import os

import matplotlib.pyplot as plt


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--denah", required=True)
    p.add_argument("--lantai", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    img = plt.imread(a.denah)

    fig, ax = plt.subplots(figsize=(14, 8))
    ax.imshow(img)
    ax.set_title("Langkah 1: klik 2 titik referensi jarak")
    (x1, y1), (x2, y2) = plt.ginput(2, timeout=0)
    ax.plot([x1, x2], [y1, y2], "b-o")
    fig.canvas.draw()
    meter = float(input("Jarak sebenarnya antara 2 titik tsb (meter): "))
    scale = math.hypot(x2 - x1, y2 - y1) / meter
    print("Skala = %.2f piksel/meter" % scale)
    with open(os.path.join(os.path.dirname(a.out) or ".", "skala_L%s.txt" % a.lantai), "w") as f:
        f.write("%.3f\n" % scale)

    ax.set_title("Langkah 2: klik titik ukur (klik kanan = batal, Enter = selesai)")
    fig.canvas.draw()
    pts = plt.ginput(n=-1, timeout=0, mouse_add=1, mouse_pop=3, mouse_stop=None)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["titik_id", "lantai", "x_px", "y_px", "jenis_area", "keterangan"])
        for i, (x, y) in enumerate(pts, 1):
            tid = "L%s-%03d" % (a.lantai, i)
            w.writerow([tid, a.lantai, int(round(x)), int(round(y)), "", ""])
            ax.plot(x, y, "ro", ms=6)
            ax.annotate(tid[3:], (x, y), fontsize=8, color="r", xytext=(4, 4), textcoords="offset points")
    ax.set_title("Lantai %s - %d titik ukur (skala %.1f px/m)" % (a.lantai, len(pts), scale))
    ax.axis("off")
    fig.savefig(os.path.splitext(a.out)[0] + ".png", dpi=200, bbox_inches="tight")
    print("Tersimpan %d titik -> %s" % (len(pts), a.out))


if __name__ == "__main__":
    main()
