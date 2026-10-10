#!/usr/bin/env python3
"""
mark_ap_fisik.py

Klik posisi 7 AP fisik pada denah RAW final, lalu simpan koordinat pixel
dalam format yang dibaca analyze.py.

PENTING:
- Koordinat HARUS berasal dari denah_L4_raw.png yang sama dengan titik_L4.csv.
- denah_L4_with_AP.png hanya ditampilkan sebagai REFERENSI visual karena
  ukuran pixelnya berbeda dari denah raw, jadi koordinatnya tidak boleh dipakai langsung.
"""

import argparse
import csv
import os
import matplotlib.pyplot as plt

APS = [
    ("AP-L4-01", "F4.14"),
    ("AP-L4-02", "F4.10"),
    ("AP-L4-03", "F4.13"),
    ("AP-L4-04", "F4.9"),
    ("AP-L4-05", "F4.4"),
    ("AP-L4-06", "F4.6"),
    ("AP-L4-07", "F4.1"),
]


def ambil_satu_klik(fig, ax):
    """Tunggu satu klik kiri yang benar-benar berada pada axes denah RAW."""
    titik = []

    def onclick(event):
        if event.button == 1 and event.inaxes is ax and event.xdata is not None and event.ydata is not None:
            titik.append((event.xdata, event.ydata))

    cid = fig.canvas.mpl_connect("button_press_event", onclick)

    while not titik:
        if not plt.fignum_exists(fig.number):
            fig.canvas.mpl_disconnect(cid)
            raise SystemExit("Dibatalkan: window ditutup sebelum semua AP ditandai.")
        plt.pause(0.05)

    fig.canvas.mpl_disconnect(cid)
    return titik[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--denah",
        default="denah/denah_L4_raw.png",
        help="Denah RAW final; harus sama dengan denah untuk titik_L4.csv",
    )
    p.add_argument(
        "--referensi",
        default="denah/denah_L4_with_AP.png",
        help="Denah dengan tanda AP; hanya referensi visual",
    )
    p.add_argument("--out", default="data/ap_fisik_L4.csv")
    p.add_argument("--preview", default="data/ap_fisik_L4_preview.png")
    a = p.parse_args()

    if not os.path.isfile(a.denah):
        raise SystemExit(f"ERROR: denah tidak ditemukan: {a.denah}")
    if not os.path.isfile(a.referensi):
        raise SystemExit(f"ERROR: referensi tidak ditemukan: {a.referensi}")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(a.preview) or ".", exist_ok=True)

    raw = plt.imread(a.denah)
    ref = plt.imread(a.referensi)

    fig, (ax_ref, ax_raw) = plt.subplots(1, 2, figsize=(18, 8))

    ax_ref.imshow(ref)
    ax_ref.set_title("REFERENSI AP — jangan diklik")
    ax_ref.axis("off")

    ax_raw.imshow(raw)
    ax_raw.set_title("DENAH RAW — klik di panel ini")
    ax_raw.axis("off")

    hasil = []

    print("=== MARK AP FISIK L4 ===")
    print("Panel KIRI  : referensi posisi AP.")
    print("Panel KANAN : denah RAW; SEMUA klik dilakukan di sini.")
    print("Klik sedekat mungkin ke pusat lokasi AP fisik.")
    print()

    for ap_id, ket in APS:
        ax_raw.set_title(f"KLIK {ap_id} — {ket} | panel KANAN")
        fig.canvas.draw_idle()

        print(f"{ap_id} ({ket}) -> klik 1x pada panel KANAN...")
        x, y = ambil_satu_klik(fig, ax_raw)

        x_i, y_i = int(round(x)), int(round(y))
        hasil.append((ap_id, x_i, y_i, ket))

        ax_raw.plot(
            x, y, "s",
            markersize=9,
            markerfacecolor="none",
            markeredgewidth=1.8,
        )
        ax_raw.text(
            x + 8, y - 8, ap_id,
            fontsize=8,
            bbox=dict(boxstyle="round,pad=0.2", alpha=0.7),
        )
        fig.canvas.draw_idle()

        print(f"  x={x_i}, y={y_i}")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ap_id", "x_px", "y_px", "keterangan"])
        writer.writerows(hasil)

    ax_raw.set_title("AP fisik L4 — preview pada denah RAW")
    fig.savefig(a.preview, dpi=180, bbox_inches="tight")

    print()
    print(f"Tersimpan {len(hasil)} AP -> {a.out}")
    print(f"Preview -> {a.preview}")
    print()
    print("Isi koordinat:")
    for ap_id, x, y, ket in hasil:
        print(f"  {ap_id}: ({x}, {y}) — {ket}")

    plt.show()


if __name__ == "__main__":
    main()
