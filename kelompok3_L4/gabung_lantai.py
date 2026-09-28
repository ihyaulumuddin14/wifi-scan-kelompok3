#!/usr/bin/env python3
"""
gabung_lantai.py - gabungkan hasil 3 kelompok (lantai 2, 3, 4) untuk diskusi pleno.

  python3 gabung_lantai.py --ap 2:output/L2_ap.csv 3:output/L3_ap.csv 4:output/L4_ap.csv \
      --titik 2:output/L2_titik.csv 3:output/L3_titik.csv 4:output/L4_titik.csv \
      --ringkasan output/L2_ringkasan.txt output/L3_ringkasan.txt output/L4_ringkasan.txt \
      --out output/gedung

- Untuk setiap radio AP: RSSI maksimum yang terukur di tiap lantai.
- Lantai "asal" AP = lantai dengan RSSI maksimum terkuat.
- Selisih RSSI maks lantai asal vs lantai tetangga = estimasi kasar atenuasi lantai
  (mengandung juga pengaruh jarak; lihat modul Bagian 9.4).
- (--titik) Titik yang AP terkuatnya berasal dari lantai lain. Karena SSID sama di semua
  lantai, klien di titik tersebut bisa tersambung ke AP lantai lain.
"""
import argparse
import sys

import pandas as pd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ap", nargs="+", required=True, help="lantai:path_ap.csv")
    p.add_argument("--titik", nargs="*", default=[], help="lantai:path_titik.csv (keluaran analyze.py)")
    p.add_argument("--ringkasan", nargs="*", default=[])
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # konsol Windows: jangan crash karena emoji SSID
        sys.stdout.reconfigure(errors="replace")

    frames = []
    for item in a.ap:
        lantai, path = item.split(":", 1)
        d = pd.read_csv(path)
        d["lantai"] = int(lantai)
        frames.append(d)
    df = pd.concat(frames)
    # nama radio = BSSID milik SSID target (ditetapkan analyze.py), sehingga sama di semua lantai
    norm = lambda r: r
    kanal = df.groupby("radio")["channel"].agg(lambda c: "/".join(str(int(v)) for v in sorted(set(c))))
    piv = df.pivot_table(index="radio", columns="lantai", values="rssi_maks", aggfunc="max")
    lantai_cols = sorted(piv.columns)
    piv = piv.reset_index()
    piv.insert(1, "channel", piv.radio.map(kanal))
    piv["lantai_asal"] = piv[lantai_cols].idxmax(axis=1)
    rows = []
    for _, r in piv.iterrows():
        asal = r.lantai_asal
        for tetangga in (asal - 1, asal + 1):
            if tetangga in lantai_cols and pd.notna(r[tetangga]):
                rows.append({"radio": r.radio, "channel": r.channel, "lantai_asal": asal,
                             "lantai_tetangga": tetangga, "rssi_asal": r[asal],
                             "rssi_tetangga": r[tetangga],
                             "selisih_db": round(r[asal] - r[tetangga], 1)})
    bleed = pd.DataFrame(rows)
    piv.to_csv(a.out + "_ap_per_lantai.csv", index=False)
    bleed.to_csv(a.out + "_floor_bleed.csv", index=False)

    print("RSSI maks per radio per lantai (dBm):")
    print(piv.to_string(index=False))
    print("\nJumlah radio SSID kampus per lantai asal:")
    print(piv.lantai_asal.value_counts().sort_index().to_string())
    if len(bleed):
        print("\nFloor bleed (radio terdengar di lantai tetangga):")
        print(bleed.to_string(index=False))
        print("\nMedian selisih RSSI antar-lantai: %.1f dB (n=%d)" % (bleed.selisih_db.median(), len(bleed)))
    if a.titik:
        asal = dict(zip(piv.radio, piv.lantai_asal))
        rows = []
        for item in a.titik:
            lantai, path = item.split(":", 1)
            t = pd.read_csv(path)
            for _, r in t.dropna(subset=["rssi1"]).iterrows():
                la = asal.get(norm(r.radio1))
                rows.append({"lantai": int(lantai), "titik_id": r.titik_id, "rssi1": r.rssi1,
                             "radio1": norm(r.radio1), "lantai_asal_ap": la,
                             "dari_lantai_lain": la is not None and la != int(lantai)})
        tl = pd.DataFrame(rows)
        tl.to_csv(a.out + "_titik_lantai_lain.csv", index=False)
        print("\nTitik yang AP terkuatnya berasal dari LANTAI LAIN (SSID sama -> klien bisa tersambung ke sana):")
        for lt, g in tl.groupby("lantai"):
            lain = g[g.dari_lantai_lain]
            print("  Lantai %d: %d dari %d titik (%.0f%%)%s" % (
                lt, len(lain), len(g), 100.0 * len(lain) / len(g),
                "" if lain.empty else " -> " + ", ".join("%s(L%d)" % (x.titik_id, x.lantai_asal_ap)
                                                         for x in lain.itertuples())))
    for path in a.ringkasan:
        print("\n" + open(path, encoding="utf-8").read())


if __name__ == "__main__":
    main()
