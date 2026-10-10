#!/usr/bin/env python3
"""
prepare_passive_L4.py

Membersihkan data passive L4 zona B lalu menggabungkannya dengan zona A.

Input:
  data/passive_L4_A.csv   -> CSV normal, delimiter koma
  data/passive_L4_B.csv   -> delimiter titik-koma, RSSI/utilisasi tersimpan x10,
                             band 2.4 tersimpan sebagai 24

Output:
  data/passive_L4_B_clean.csv
  data/passive_L4.csv

Script ini TIDAK mengubah file raw A/B.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from collections import defaultdict


EXPECTED_HEADER = [
    "timestamp", "lantai", "titik_id", "x_px", "y_px", "slot_waktu",
    "device", "scan_ke", "ssid", "bssid", "freq_mhz", "channel", "band",
    "width_mhz", "rssi_dbm", "noise_dbm", "sta_count", "ch_util_pct",
    "security", "pmf", "catatan",
]


def die(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def read_csv(path: str, delimiter: str) -> tuple[list[str], list[dict[str, str]]]:
    if not os.path.isfile(path):
        die(f"file tidak ditemukan: {path}")

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if reader.fieldnames is None:
            die(f"header tidak ditemukan di {path}")
        header = [h.strip() for h in reader.fieldnames]

        missing = [c for c in EXPECTED_HEADER if c not in header]
        if missing:
            die(f"kolom kurang di {path}: {', '.join(missing)}")

        rows = []
        for row in reader:
            clean = {}
            for h in header:
                v = row.get(h, "")
                clean[h] = "" if v is None else str(v).strip()
            rows.append(clean)

    return header, rows


def fmt_num(v: float, decimals: int = 1) -> str:
    if not math.isfinite(v):
        return ""
    s = f"{v:.{decimals}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def to_float(value: str, field: str, row_no: int) -> float | None:
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        die(f"nilai {field!r} tidak valid pada baris data B #{row_no}: {value!r}")


def clean_b(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []

    for i, row in enumerate(rows, start=1):
        r = dict(row)

        # Zona B tersimpan 10x untuk RSSI: -430 => -43.0 dBm.
        rssi = to_float(r["rssi_dbm"], "rssi_dbm", i)
        if rssi is not None:
            r["rssi_dbm"] = fmt_num(rssi / 10.0, 1)

        # Zona B tersimpan 10x untuk channel utilization: 663 => 66.3%.
        util = to_float(r["ch_util_pct"], "ch_util_pct", i)
        if util is not None:
            r["ch_util_pct"] = fmt_num(util / 10.0, 1)

        # Band 2.4 GHz tersimpan sebagai "24". Band 5 GHz tetap "5".
        band = r["band"].strip()
        if band in {"24", "24.0"}:
            r["band"] = "2.4"
        elif band in {"5", "5.0"}:
            r["band"] = "5"
        else:
            die(f"band tidak dikenal pada baris data B #{i}: {band!r}")

        # Rapikan MAC/teks kunci tanpa mengubah makna data.
        r["bssid"] = r["bssid"].lower().strip()
        r["ssid"] = r["ssid"].strip()
        r["titik_id"] = r["titik_id"].strip()

        out.append(r)

    return out


def point_number(titik_id: str) -> int:
    m = re.fullmatch(r"L4-(\d{3})", titik_id.strip())
    return int(m.group(1)) if m else 9999


def validate(rows_a: list[dict[str, str]],
             rows_b_raw: list[dict[str, str]],
             rows_b: list[dict[str, str]],
             merged: list[dict[str, str]]) -> None:

    def scan_counts(rows):
        d = defaultdict(set)
        for r in rows:
            d[r["titik_id"]].add(r["scan_ke"])
        return {k: len(v) for k, v in d.items()}

    # A: calibration + titik 011..029
    pts_a = sorted({r["titik_id"] for r in rows_a}, key=point_number)
    pts_b = sorted({r["titik_id"] for r in rows_b}, key=point_number)
    pts_all = sorted({r["titik_id"] for r in merged}, key=point_number)

    expected_a = ["L4-000"] + [f"L4-{i:03d}" for i in range(11, 30)]
    expected_b = [f"L4-{i:03d}" for i in range(1, 11)]
    expected_all = [f"L4-{i:03d}" for i in range(0, 30)]

    if pts_a != expected_a:
        print("WARNING: daftar titik passive A tidak persis sesuai ekspektasi.")
        print("  Ditemukan:", ", ".join(pts_a))
    if pts_b != expected_b:
        print("WARNING: daftar titik passive B tidak persis L4-001..L4-010.")
        print("  Ditemukan:", ", ".join(pts_b))
    if pts_all != expected_all:
        print("WARNING: titik gabungan tidak lengkap L4-000..L4-029.")
        print("  Ditemukan:", ", ".join(pts_all))

    scans = scan_counts(merged)
    bad_scans = []
    for p in expected_all:
        expected = 5 if p == "L4-000" else 3
        actual = scans.get(p, 0)
        if actual != expected:
            bad_scans.append((p, expected, actual))

    if bad_scans:
        print("WARNING: jumlah scan tidak sesuai:")
        for p, exp, act in bad_scans:
            print(f"  {p}: expected {exp}, actual {act}")
    else:
        print("OK: scan count -> L4-000 = 5 scan; L4-001..L4-029 = 3 scan/titik")

    # Numeric sanity after cleanup.
    def numeric_values(rows, col):
        vals = []
        for r in rows:
            v = r[col].strip()
            if v != "":
                try:
                    vals.append(float(v))
                except ValueError:
                    die(f"{col} bukan angka setelah cleaning: {v!r}")
        return vals

    rssi = numeric_values(merged, "rssi_dbm")
    util = numeric_values(merged, "ch_util_pct")
    bands = sorted({r["band"] for r in merged if r["band"]})

    if rssi:
        print(f"OK: RSSI gabungan = {min(rssi):.1f} .. {max(rssi):.1f} dBm")
        if min(rssi) < -120 or max(rssi) > -10:
            print("WARNING: range RSSI terlihat tidak wajar.")
    if util:
        print(f"OK: channel utilization = {min(util):.1f} .. {max(util):.1f}%")
        if min(util) < 0 or max(util) > 100:
            print("WARNING: channel utilization di luar 0..100%.")
    print("OK: band =", ", ".join(bands))

    # Pastikan transformasi B memang benar-benar terjadi.
    raw_rssi = numeric_values(rows_b_raw, "rssi_dbm")
    clean_rssi = numeric_values(rows_b, "rssi_dbm")
    raw_util = numeric_values(rows_b_raw, "ch_util_pct")
    clean_util = numeric_values(rows_b, "ch_util_pct")
    if raw_rssi and clean_rssi:
        print(
            f"CHECK B RSSI: raw {min(raw_rssi):.0f}..{max(raw_rssi):.0f} "
            f"-> clean {min(clean_rssi):.1f}..{max(clean_rssi):.1f}"
        )
    if raw_util and clean_util:
        print(
            f"CHECK B util: raw {min(raw_util):.0f}..{max(raw_util):.0f} "
            f"-> clean {min(clean_util):.1f}..{max(clean_util):.1f}"
        )


def write_csv(path: str, header: list[str], rows: list[dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=header,
            delimiter=",",
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="data/passive_L4_A.csv")
    ap.add_argument("--b", default="data/passive_L4_B.csv")
    ap.add_argument("--out-b", default="data/passive_L4_B_clean.csv")
    ap.add_argument("--out", default="data/passive_L4.csv")
    args = ap.parse_args()

    header_a, rows_a = read_csv(args.a, ",")
    header_b, rows_b_raw = read_csv(args.b, ";")

    # Gunakan urutan kolom A sebagai format final.
    if set(header_a) != set(header_b):
        only_a = sorted(set(header_a) - set(header_b))
        only_b = sorted(set(header_b) - set(header_a))
        die(f"header A/B berbeda. only A={only_a}, only B={only_b}")

    rows_b = clean_b(rows_b_raw)

    # Tulis B clean terlebih dahulu.
    write_csv(args.out_b, header_a, rows_b)

    # Merge A + B lalu urutkan berdasarkan titik; urutan asli di dalam titik dipertahankan.
    combined = []
    seq = 0
    for source_rows in (rows_a, rows_b):
        for r in source_rows:
            rr = dict(r)
            rr["_seq"] = seq
            seq += 1
            combined.append(rr)

    combined.sort(
        key=lambda r: (
            point_number(r["titik_id"]),
            int(r["scan_ke"]) if r["scan_ke"].isdigit() else 999,
            r["_seq"],
        )
    )

    merged = []
    for r in combined:
        r = dict(r)
        r.pop("_seq", None)
        merged.append(r)

    write_csv(args.out, header_a, merged)

    print()
    print("=== VALIDASI PASSIVE L4 ===")
    print(f"A raw       : {len(rows_a)} rows")
    print(f"B raw       : {len(rows_b_raw)} rows")
    print(f"B clean     : {len(rows_b)} rows")
    print(f"Merged      : {len(merged)} rows")
    validate(rows_a, rows_b_raw, rows_b, merged)

    print()
    print("Tersimpan:")
    print(f"  {args.out_b}")
    print(f"  {args.out}")
    print()
    print("File raw A/B TIDAK diubah.")


if __name__ == "__main__":
    main()
