#!/usr/bin/env python3
"""
analyze.py - analisis kualitas sinyal Wi-Fi 5 GHz satu lantai dalam satu perintah.

Contoh:
  python3 analyze.py --passive data/passive_L3.csv --ssid "NAMA_SSID" \
      --denah denah/L3.png --scale 20 --out output/L3 --titik data/titik_L3.csv \
      [--active data/active_L3.csv] [--ap-fisik data/ap_fisik_L3.csv]

Keluaran (prefix --out):
  _titik.csv       metrik + status + penyebab per titik ukur
  _ap.csv          estimasi posisi AP (radio) dari data RSSI
  _area_lemah.csv  kelompok titik lemah yang berdekatan (weak-spot area)
  _heatmap.png     heatmap RSSI AP terkuat + kontur -67/-75 dBm
  _status.png      peta status titik, area lemah, dan posisi AP
  _kanal.png       distribusi kanal 5 GHz
  _ringkasan.txt   ringkasan angka untuk laporan
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.ticker
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.interpolate import griddata
from scipy.spatial import cKDTree

# ---------------- Ambang kriteria (lihat Bagian 4 modul) ----------------
RSSI_BAIK = -67      # >= : baik untuk semua aplikasi
RSSI_LEMAH = -70     # <  : lemah
RSSI_KRITIS = -80    # <  : blank spot
RSSI_SEKUNDER = -75  # AP kedua minimal (roaming/redundansi)
CCI_RSSI = -85       # radio lain di kanal overlap yang dihitung sebagai CCI
CCI_MAX = 2          # > : co-channel interference tinggi
SNR_BAIK, SNR_BURUK = 25, 15
UTIL_MAX = 50        # % channel utilization (BSS Load)
DOWN_MIN = 10        # Mbps
LOSS_MAX = 2         # %
RTT_MAX = 50         # ms

AP_TINGGI, AP_SEDANG = -55, -65  # RSSI maks radio: di lantai ini / mungkin tembus dari lantai lain

LEVEL = {"BAIK": 0, "PERHATIAN": 1, "BURUK": 2, "KRITIS": 3}
WARNA = {"BAIK": "#1a9850", "PERHATIAN": "#fee08b", "BURUK": "#f46d43", "KRITIS": "#a50026"}

BLOK_40 = [(36, 40), (44, 48), (52, 56), (60, 64), (100, 104), (108, 112), (116, 120),
           (124, 128), (132, 136), (140, 144), (149, 153), (157, 161)]
BLOK_80 = [(36, 48), (52, 64), (100, 112), (116, 128), (132, 144), (149, 161)]
BLOK_160 = [(36, 64), (100, 128)]


def kanal_terpakai(ch, width):
    """Himpunan kanal 20 MHz yang diduduki (untuk menghitung overlap/CCI)."""
    ch = int(ch)
    width = int(width) if pd.notna(width) else 20
    if ch <= 14:  # 2,4 GHz: 22 MHz, overlap +-4 kanal
        return set(range(ch - 4, ch + 5))
    blok = {40: BLOK_40, 80: BLOK_80, 160: BLOK_160}.get(width)
    if blok:
        for lo, hi in blok:
            if lo <= ch <= hi:
                return set(range(lo, hi + 1, 4))
    return {ch}


def rata_linear(dbm):
    """Rata-rata daya di domain linear (mW), dikembalikan dalam dBm."""
    return 10 * np.log10(np.mean(np.power(10, np.asarray(dbm, float) / 10)))


def keluar(pesan):
    print("ERROR: " + pesan)
    sys.exit(1)


def muat_passive(path, band, titik=None):
    df = pd.read_csv(path, dtype={"band": str, "bssid": str, "ssid": str, "titik_id": str})
    for c in ("titik_id", "ssid", "bssid", "channel", "rssi_dbm"):
        if c not in df:
            keluar("kolom '%s' tidak ada di %s" % (c, path))
    df["titik_id"] = df["titik_id"].str.strip()
    if titik:  # koordinat dari titik_L{n}.csv (hasil mark_points.py) menggantikan isi passive
        t = pd.read_csv(titik, dtype={"titik_id": str})[["titik_id", "x_px", "y_px"]]
        t["titik_id"] = t["titik_id"].str.strip()
        dup = t.titik_id[t.titik_id.duplicated()].unique()
        if len(dup):
            print("PERINGATAN: titik_id ganda di %s, dipakai yang pertama: %s" % (titik, ", ".join(dup)))
            t = t.drop_duplicates("titik_id")
        df = df.drop(columns=[c for c in ("x_px", "y_px") if c in df]).merge(t, on="titik_id", how="left")
    elif "x_px" not in df or "y_px" not in df:
        keluar("kolom x_px/y_px tidak ada. Sertakan --titik data/titik_L{n}.csv")
    df["x_px"] = pd.to_numeric(df["x_px"], errors="coerce")
    df["y_px"] = pd.to_numeric(df["y_px"], errors="coerce")
    hilang = sorted(df.loc[df.x_px.isna() | df.y_px.isna(), "titik_id"].unique())
    if hilang and len(hilang) == df.titik_id.nunique():
        keluar("semua titik tidak punya koordinat. Sertakan --titik data/titik_L{n}.csv "
               "(hasil mark_points.py) atau isi kolom x_px/y_px")
    if hilang:
        print("PERINGATAN: titik tanpa koordinat (diabaikan): %s" % ", ".join(hilang))
        df = df.dropna(subset=["x_px", "y_px"])
    df["rssi_dbm"] = pd.to_numeric(df["rssi_dbm"], errors="coerce")
    df["channel"] = pd.to_numeric(df["channel"], errors="coerce")
    df = df.dropna(subset=["rssi_dbm", "channel"])
    df["ssid"] = df["ssid"].fillna("")
    df["bssid"] = df["bssid"].fillna("").str.lower().str.strip()
    kosong = df["bssid"] == ""
    if kosong.any():
        print("PERINGATAN: %d baris tanpa BSSID diabaikan (BSSID wajib dicatat)" % kosong.sum())
        df = df[~kosong]
    dari_kanal = pd.Series(np.where(df["channel"] >= 32, "5", "2.4"), index=df.index)
    if "band" not in df:
        df["band"] = dari_kanal
    df["band"] = df["band"].str.strip().str.replace(r"\.0$", "", regex=True)
    df["band"] = df["band"].where(df["band"].notna() & (df["band"] != ""), dari_kanal)
    if "width_mhz" not in df:
        df["width_mhz"] = 20
    for c in ["noise_dbm", "ch_util_pct", "sta_count"]:
        if c not in df:
            df[c] = np.nan
    hasil = df[df["band"] == band].copy()
    if hasil.empty:
        keluar("tidak ada data band %s GHz. Band yang ada di data: %s"
               % (band, ", ".join(sorted(df["band"].dropna().unique())) or "-"))
    return hasil


def kelompok_radio(df, target=None, maks_selisih=4.0):
    """Kelompokkan BSSID menjadi radio fisik. Satu radio AP bisa memancarkan beberapa SSID
    (di FILKOM: WiFi-UB.x, eduroam, FILKOM EVENT) dengan BSSID yang mirip. Dua BSSID dianggap
    satu radio bila: kanal sama, SSID berbeda, MAC hanya beda 1 oktet, dan median selisih
    RSSI di titik yang sama <= maks_selisih dB. BSSID ber-SSID sama TIDAK pernah digabung.
    Nama radio = BSSID milik SSID target (konsisten antar-lantai), selain itu BSSID terkecil."""
    info = df.groupby("bssid").agg(ssid=("ssid", "first"), channel=("channel", lambda c: c.mode().iloc[0]))
    rssi = df.groupby(["bssid", "titik_id"])["rssi_dbm"].mean().unstack()
    induk = {b: b for b in info.index}

    def akar(b):
        while induk[b] != b:
            induk[b] = induk[induk[b]]
            b = induk[b]
        return b

    for _, grup in info.groupby("channel"):
        anggota = list(grup.index)
        for i, b1 in enumerate(anggota):
            o1 = b1.split(":")
            for b2 in anggota[i + 1:]:
                o2 = b2.split(":")
                if info.at[b1, "ssid"] == info.at[b2, "ssid"] or len(o1) != 6 or len(o2) != 6:
                    continue
                if sum(x != y for x, y in zip(o1, o2)) > 1:
                    continue
                beda = (rssi.loc[b1] - rssi.loc[b2]).abs().dropna()
                if len(beda) and beda.median() <= maks_selisih:
                    induk[akar(b1)] = akar(b2)
    kelompok = {}
    for b in info.index:
        kelompok.setdefault(akar(b), []).append(b)
    nama = {}
    for anggota in kelompok.values():
        tg = [b for b in anggota if info.at[b, "ssid"] == target]
        wakil = max(tg, key=lambda b: rssi.loc[b].mean()) if tg else min(anggota)
        for b in anggota:
            nama[b] = wakil
    multi = [g for g in kelompok.values() if len(g) > 1]
    if multi:
        print("Info: %d radio memancarkan >1 SSID (BSSID digabung per radio)" % len(multi))
    return df["bssid"].map(nama)


def metrik_titik(df, ssid):
    per_bssid = (df.groupby(["titik_id", "x_px", "y_px", "ssid", "bssid", "radio"])
                 .agg(rssi=("rssi_dbm", rata_linear), channel=("channel", "first"),
                      width=("width_mhz", "max"), noise=("noise_dbm", "mean"),
                      util=("ch_util_pct", "mean"), sta=("sta_count", "mean"))
                 .reset_index())
    # satu baris per radio fisik (SSID lain dari radio yang sama tidak dihitung dua kali)
    per_radio = (per_bssid.sort_values("rssi", ascending=False)
                 .groupby(["titik_id", "radio"]).first().reset_index())
    target = (per_bssid[per_bssid.ssid == ssid].sort_values("rssi", ascending=False)
              .drop_duplicates(["titik_id", "radio"]))

    rows = []
    for (tid, x, y), g in per_radio.groupby(["titik_id", "x_px", "y_px"]):
        tg = target[target.titik_id == tid]
        r = {"titik_id": tid, "x_px": x, "y_px": y, "n_radio_terlihat": len(g)}
        if tg.empty:
            r.update(rssi1=np.nan, rssi2=np.nan, bssid1="", radio1="", channel1=np.nan,
                     width1=np.nan, cci=np.nan, snr=np.nan, util1=np.nan, sta1=np.nan)
            rows.append(r)
            continue
        p = tg.iloc[0]
        occ = kanal_terpakai(p.channel, p.width)
        lain = g[(g.radio != p.radio) & (g.rssi >= CCI_RSSI)]
        cci = sum(1 for _, o in lain.iterrows() if occ & kanal_terpakai(o.channel, o.width))
        r.update(rssi1=round(p.rssi, 1),
                 rssi2=round(tg.iloc[1].rssi, 1) if len(tg) > 1 else np.nan,
                 bssid1=p.bssid, radio1=p.radio, channel1=int(p.channel), width1=p.width, cci=cci,
                 snr=round(p.rssi - p.noise, 1) if pd.notna(p.noise) else np.nan,
                 util1=p.util, sta1=p.sta)
        rows.append(r)
    return pd.DataFrame(rows), per_bssid


def nilai_status(r):
    lv, utama, sebab = "BAIK", "", []

    def naik(level, alasan):
        nonlocal lv, utama
        sebab.append(alasan)
        if LEVEL[level] > LEVEL[lv]:
            lv, utama = level, alasan

    if pd.isna(r.rssi1) or r.rssi1 < RSSI_KRITIS:
        naik("KRITIS", "blank spot (RSSI<%d)" % RSSI_KRITIS)
    elif r.rssi1 < RSSI_LEMAH:
        naik("BURUK", "cakupan lemah (RSSI<%d)" % RSSI_LEMAH)
    elif r.rssi1 < RSSI_BAIK:
        naik("PERHATIAN", "RSSI marginal")
    if pd.notna(r.rssi1) and (pd.isna(r.rssi2) or r.rssi2 < RSSI_SEKUNDER):
        naik("PERHATIAN", "tanpa AP cadangan (roaming)")
    if pd.notna(r.cci) and r.cci > CCI_MAX:
        naik("PERHATIAN", "co-channel interference (%d radio)" % r.cci)
    if pd.notna(r.snr):
        if r.snr < SNR_BURUK:
            naik("BURUK", "SNR rendah")
        elif r.snr < SNR_BAIK:
            naik("PERHATIAN", "SNR marginal")
    if pd.notna(r.util1) and r.util1 > UTIL_MAX:
        naik("PERHATIAN", "kanal padat (util %.0f%%)" % r.util1)
    if pd.notna(r.get("down_mbps")) and r.down_mbps < DOWN_MIN:
        naik("BURUK", "throughput rendah")
    if pd.notna(r.get("loss_pct")) and r.loss_pct > LOSS_MAX:
        naik("BURUK", "packet loss")
    if pd.notna(r.get("rtt_avg_ms")) and r.rtt_avg_ms > RTT_MAX:
        naik("PERHATIAN", "latensi tinggi")
    return pd.Series({"status": lv, "penyebab_utama": utama, "penyebab": "; ".join(sebab)})


def kelompok_lemah(t, scale, radius_m):
    lemah = t[t.status.isin(["BURUK", "KRITIS"])].reset_index(drop=True)
    parent = list(range(len(lemah)))

    def akar(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    xy = lemah[["x_px", "y_px"]].to_numpy(float) / scale
    for i in range(len(lemah)):
        for j in range(i + 1, len(lemah)):
            if np.hypot(*(xy[i] - xy[j])) <= radius_m:
                parent[akar(i)] = akar(j)
    lemah["area"] = [akar(i) for i in range(len(lemah))]
    out = []
    for k, (_, g) in enumerate(lemah.groupby("area"), 1):
        sebab = g.penyebab_utama.str.replace(r" \(.*\)", "", regex=True).value_counts()
        out.append({"area": "A%d" % k, "jumlah_titik": len(g), "titik": ", ".join(g.titik_id),
                    "x_px": g.x_px.mean(), "y_px": g.y_px.mean(),
                    "rssi_rata": round(g.rssi1.mean(), 1),
                    "status_terburuk": max(g.status, key=LEVEL.get),
                    "penyebab_dominan": sebab.index[0]})
    return pd.DataFrame(out)


def estimasi_ap(per_bssid, ssid, top=3):
    t = per_bssid[per_bssid.ssid == ssid]
    rows = []
    for radio, g in t.groupby("radio"):
        g = g.sort_values("rssi", ascending=False).drop_duplicates("titik_id").head(top)
        w = np.power(10, g.rssi / 10)
        rows.append({"radio": radio, "channel": int(g.channel.iloc[0]),
                     "width": g.width.iloc[0], "rssi_maks": round(g.rssi.iloc[0], 1),
                     "titik_terkuat": g.titik_id.iloc[0],
                     "x_est": np.average(g.x_px, weights=w), "y_est": np.average(g.y_px, weights=w),
                     "keyakinan": "tinggi" if g.rssi.iloc[0] >= AP_TINGGI else
                                  ("sedang (mungkin lantai lain)" if g.rssi.iloc[0] >= AP_SEDANG else
                                   "rendah (lantai/area lain)")})
    return pd.DataFrame(rows)


def interpolasi(xy, z, gx, gy, scale, radius_m=5.0):
    """Linear (Delaunay) bila titik tersebar 2D; bila gagal (mis. titik segaris di koridor)
    pakai IDW yang dibatasi radius_m dari titik ukur terdekat."""
    if len(xy) >= 3:
        try:
            gz = griddata(xy, z, (gx, gy), method="linear")
            # jangan mengisi area yang jauh dari titik ukur (mis. blok ruang di tengah koridor cincin)
            jarak, _ = cKDTree(xy).query(np.column_stack([gx.ravel(), gy.ravel()]))
            gz[(jarak > 6.0 * scale).reshape(gz.shape)] = np.nan
            if np.isfinite(gz).any():
                return gz, "linear (maks 6 m dari titik)"
        except Exception:  # QhullError: titik segaris/terlalu sedikit
            pass
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    gz = np.full(len(pts), np.nan)
    for i in range(0, len(pts), 20000):  # per blok agar hemat memori
        d = np.hypot(pts[i:i + 20000, None, 0] - xy[None, :, 0], pts[i:i + 20000, None, 1] - xy[None, :, 1])
        w = 1.0 / np.maximum(d, 1.0) ** 2
        val = (w * z).sum(axis=1) / w.sum(axis=1)
        val[d.min(axis=1) > radius_m * scale] = np.nan
        gz[i:i + 20000] = val
    return gz.reshape(gx.shape), "IDW (radius %.0f m)" % radius_m


def judul(teks):
    """Buang karakter di luar BMP (emoji) yang tidak ada di font matplotlib."""
    return "".join(ch for ch in teks if ord(ch) < 0x10000)


def latar(ax, denah, t):
    if denah and os.path.exists(denah):
        img = plt.imread(denah)
        ax.imshow(img)
        return img.shape[1], img.shape[0]
    w, h = t.x_px.max() * 1.1 + 1, t.y_px.max() * 1.1 + 1
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_aspect("equal")
    return w, h


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--passive", required=True)
    p.add_argument("--ssid", required=True)
    p.add_argument("--band", default="5")
    p.add_argument("--denah")
    p.add_argument("--scale", type=float, required=True, help="piksel per meter pada denah")
    p.add_argument("--out", required=True, help="prefix keluaran, mis. output/L3")
    p.add_argument("--titik", help="titik_L{n}.csv: sumber koordinat titik (disarankan)")
    p.add_argument("--active")
    p.add_argument("--ap-fisik", help="CSV ap_id,x_px,y_px hasil observasi fisik")
    p.add_argument("--radius", type=float, default=8, help="radius (m) pengelompokan area lemah")
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # konsol Windows: jangan crash karena emoji SSID
        sys.stdout.reconfigure(errors="replace")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)

    df = muat_passive(a.passive, a.band, a.titik)
    if not (df["ssid"] == a.ssid).any():
        top = df.groupby("ssid").titik_id.nunique().sort_values(ascending=False).head(10)
        keluar("SSID '%s' tidak ditemukan di band %s GHz (cek ejaan/huruf besar-kecil).\n"
               "SSID yang terdeteksi (jumlah titik):\n%s"
               % (a.ssid, a.band, "\n".join("  %r (%d)" % (k, v) for k, v in top.items())))
    df["radio"] = kelompok_radio(df, a.ssid)
    t, per_bssid = metrik_titik(df, a.ssid)
    if a.active:
        act = pd.read_csv(a.active, dtype={"titik_id": str})
        act["titik_id"] = act["titik_id"].str.strip()
        for c in ["down_mbps", "up_mbps", "rtt_avg_ms", "jitter_ms", "loss_pct"]:
            if c in act:
                act[c] = pd.to_numeric(act[c], errors="coerce")
        tanpa = sorted(set(act.titik_id) - set(t.titik_id))
        if tanpa:
            print("PERINGATAN: titik uji aktif tanpa data pasif (diabaikan): %s" % ", ".join(tanpa))
        cols = [c for c in ["down_mbps", "up_mbps", "rtt_avg_ms", "jitter_ms", "loss_pct"] if c in act]
        t = t.merge(act.groupby("titik_id")[cols].mean().reset_index(), on="titik_id", how="left")
    t[["status", "penyebab_utama", "penyebab"]] = t.apply(nilai_status, axis=1)
    area = kelompok_lemah(t, a.scale, a.radius)
    ap = estimasi_ap(per_bssid, a.ssid)
    t.to_csv(a.out + "_titik.csv", index=False)
    ap.to_csv(a.out + "_ap.csv", index=False)
    area.to_csv(a.out + "_area_lemah.csv", index=False)

    # ---- Heatmap RSSI ----
    v = t.dropna(subset=["rssi1"])
    fig, ax = plt.subplots(figsize=(14, 7))
    w, h = latar(ax, a.denah, t)
    gx, gy = np.mgrid[0:w:3, 0:h:3]
    metode = "-"
    if len(v):
        gz, metode = interpolasi(v[["x_px", "y_px"]].to_numpy(float), v.rssi1.to_numpy(float),
                                 gx, gy, a.scale)
    else:
        gz = np.full(gx.shape, np.nan)
    hm = ax.pcolormesh(gx, gy, np.ma.masked_invalid(gz), cmap="RdYlGn", vmin=-90, vmax=-40,
                       alpha=0.6, shading="auto")
    fin = gz[np.isfinite(gz)]
    lv = [l for l in (RSSI_SEKUNDER, RSSI_BAIK) if fin.size and fin.min() < l < fin.max()]
    if lv:
        cs = ax.contour(gx, gy, gz, levels=lv, linewidths=1.5, linestyles="--",
                        colors=["#d73027" if l == RSSI_SEKUNDER else "#1a9850" for l in lv])
        ax.clabel(cs, fmt="%d dBm", fontsize=8)
    ax.scatter(t.x_px, t.y_px, c="k", s=10)
    for _, r in t.iterrows():
        ax.annotate("%s\n%s" % (r.titik_id, "-" if pd.isna(r.rssi1) else "%.0f" % r.rssi1),
                    (r.x_px, r.y_px), fontsize=6, xytext=(3, 3), textcoords="offset points")
    fig.colorbar(hm, ax=ax, label="RSSI AP terkuat (dBm)", shrink=0.7)
    ax.set_title(judul("Heatmap RSSI %s GHz - SSID %s (interpolasi: %s)" % (a.band, a.ssid, metode)))
    ax.axis("off")
    fig.savefig(a.out + "_heatmap.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ---- Peta status + area lemah + AP ----
    fig, ax = plt.subplots(figsize=(14, 7))
    latar(ax, a.denah, t)
    for st in LEVEL:
        s = t[t.status == st]
        ax.scatter(s.x_px, s.y_px, c=WARNA[st], s=90, edgecolors="k", label="%s (%d)" % (st, len(s)), zorder=3)
    kotak = dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8)
    posisi = t.set_index("titik_id")[["x_px", "y_px"]]
    for _, r in area.iterrows():  # arsir 3 m di sekitar setiap titik anggota area
        anggota = posisi.loc[r.titik.split(", ")]
        for x, y in anggota.itertuples(index=False):
            ax.add_patch(plt.Circle((x, y), 3 * a.scale, fc="#d73027", ec="none", alpha=0.18, zorder=2))
        ax.annotate(r.area, (anggota.x_px.mean(), anggota.y_px.min() - 3 * a.scale), fontsize=11,
                    weight="bold", color="#a50026", ha="center", va="center", zorder=6, bbox=kotak)
    if not ap.empty:
        yakin = ap[ap.rssi_maks >= AP_TINGGI]
        mungkin = ap[(ap.rssi_maks < AP_TINGGI) & (ap.rssi_maks >= AP_SEDANG)]
        ax.scatter(yakin.x_est, yakin.y_est, marker="^", s=180, c="#2166ac", edgecolors="w",
                   label="AP estimasi (di lantai ini)", zorder=5)
        ax.scatter(mungkin.x_est, mungkin.y_est, marker="^", s=180, facecolors="none",
                   edgecolors="#2166ac", lw=1.5, label="AP estimasi (mungkin lantai lain)", zorder=5)
        for _, r in pd.concat([yakin, mungkin]).iterrows():
            ax.annotate("ch%d" % r.channel, (r.x_est, r.y_est), fontsize=8, color="#2166ac",
                        xytext=(8, 6 if r.rssi_maks >= AP_TINGGI else -14), textcoords="offset points",
                        zorder=6, bbox=kotak)
    if a.ap_fisik and os.path.exists(a.ap_fisik):
        f = pd.read_csv(a.ap_fisik)
        ax.scatter(f.x_px, f.y_px, marker="s", s=120, facecolors="none", edgecolors="#2166ac",
                   lw=2, label="AP (observasi fisik)", zorder=5)
    if len(area):
        ax.scatter([], [], s=200, c="#d73027", alpha=0.3, label="Area lemah (A1, A2, ...)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9)
    ax.set_title("Status kualitas sinyal per titik, area lemah (A1, A2, ...) dan posisi AP")
    ax.axis("off")
    fig.savefig(a.out + "_status.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ---- Distribusi kanal ----
    rad = per_bssid.drop_duplicates("radio")
    rad = rad.assign(jenis=np.where(rad.radio.isin(per_bssid[per_bssid.ssid == a.ssid].radio),
                                    "SSID kampus", "lainnya"))
    tab = rad.groupby(["channel", "jenis"]).size().unstack(fill_value=0)
    tab.index = tab.index.astype(int)
    warna_kanal = {"SSID kampus": "#2166ac", "lainnya": "#bababa"}
    ax = tab.plot(kind="bar", stacked=True, figsize=(9, 4), color=[warna_kanal[c] for c in tab.columns])
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_xlabel("Kanal primer")
    ax.set_ylabel("Jumlah radio")
    ax.set_title("Distribusi kanal %s GHz" % a.band)
    plt.tight_layout()
    plt.savefig(a.out + "_kanal.png", dpi=150)
    plt.close()

    # ---- Ringkasan ----
    n = len(t)
    pct = lambda m: 100.0 * m.sum() / n if n else 0
    L = ["Ringkasan analisis %s (band %s GHz, SSID %s)" % (a.out, a.band, a.ssid),
         "Jumlah titik ukur          : %d" % n,
         "RSSI >= %d dBm (baik)     : %.1f%%" % (RSSI_BAIK, pct(t.rssi1 >= RSSI_BAIK)),
         "RSSI >= %d dBm            : %.1f%%" % (RSSI_LEMAH, pct(t.rssi1 >= RSSI_LEMAH)),
         "RSSI <  %d dBm (blank)    : %.1f%%" % (RSSI_KRITIS, pct(t.rssi1.isna() | (t.rssi1 < RSSI_KRITIS))),
         "AP cadangan >= %d dBm     : %.1f%%" % (RSSI_SEKUNDER, pct(t.rssi2 >= RSSI_SEKUNDER)),
         "CCI > %d radio             : %.1f%%" % (CCI_MAX, pct(t.cci > CCI_MAX)),
         "RSSI median / min          : %.1f / %.1f dBm" % (t.rssi1.median(), t.rssi1.min()),
         "Radio SSID kampus terdeteksi: %d, kanal: %s" % (len(ap), sorted(ap.channel.unique().tolist()) if len(ap) else "-"),
         "", "Status titik:"]
    L += ["  %-10s: %d" % (s, (t.status == s).sum()) for s in LEVEL]
    L += ["", "Area lemah:"]
    L += ["  %s: %d titik (%s), RSSI rata %.1f, %s, penyebab dominan: %s" %
          (r.area, r.jumlah_titik, r.titik, r.rssi_rata, r.status_terburuk, r.penyebab_dominan)
          for _, r in area.iterrows()] or ["  (tidak ada)"]
    open(a.out + "_ringkasan.txt", "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
