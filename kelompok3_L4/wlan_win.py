"""
wlan_win.py - akses Wi-Fi di Windows lewat Native Wifi API (wlanapi.dll), tanpa admin.
Dipakai otomatis oleh scan_point.py dan active_point.py bila dijalankan di Windows.

- scan()  : paksa scan baru, kembalikan daftar BSS (RSSI dBm asli, bukan persen)
- link()  : info koneksi saat ini (BSSID, frekuensi, RSSI, tx rate)

Windows 11 24H2+: aktifkan Settings > Privacy & security > Location, termasuk
"Let desktop apps access your location". Tanpa izin ini, Windows menolak memberi hasil scan.
"""
import ctypes
import time
from ctypes import (POINTER, Structure, byref, c_int32, c_uint, c_uint32, c_uint64,
                    c_ushort, c_ubyte, c_void_p, c_wchar, cast, sizeof)


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_ushort), ("Data3", c_ushort),
                ("Data4", c_ubyte * 8)]


class WLAN_INTERFACE_INFO(Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", c_wchar * 256),
                ("isState", c_uint)]


class WLAN_INTERFACE_INFO_LIST(Structure):
    _fields_ = [("dwNumberOfItems", c_uint32), ("dwIndex", c_uint32),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class DOT11_SSID(Structure):
    _fields_ = [("uSSIDLength", c_uint32), ("ucSSID", c_ubyte * 32)]


class WLAN_RATE_SET(Structure):
    _fields_ = [("uRateSetLength", c_uint32), ("usRateSet", c_ushort * 126)]


class WLAN_BSS_ENTRY(Structure):
    _fields_ = [("dot11Ssid", DOT11_SSID), ("uPhyId", c_uint32), ("dot11Bssid", c_ubyte * 6),
                ("dot11BssType", c_uint), ("dot11BssPhyType", c_uint), ("lRssi", c_int32),
                ("uLinkQuality", c_uint32), ("bInRegDomain", c_ubyte), ("usBeaconPeriod", c_ushort),
                ("ullTimestamp", c_uint64), ("ullHostTimestamp", c_uint64),
                ("usCapabilityInformation", c_ushort), ("ulChCenterFrequency", c_uint32),
                ("wlanRateSet", WLAN_RATE_SET), ("ulIeOffset", c_uint32), ("ulIeSize", c_uint32)]


class WLAN_BSS_LIST(Structure):
    _fields_ = [("dwTotalSize", c_uint32), ("dwNumberOfItems", c_uint32),
                ("wlanBssEntries", WLAN_BSS_ENTRY * 1)]


class WLAN_ASSOCIATION_ATTRIBUTES(Structure):
    _fields_ = [("dot11Ssid", DOT11_SSID), ("dot11BssType", c_uint), ("dot11Bssid", c_ubyte * 6),
                ("dot11PhyType", c_uint), ("uDot11PhyIndex", c_uint32),
                ("wlanSignalQuality", c_uint32), ("ulRxRate", c_uint32), ("ulTxRate", c_uint32)]


class WLAN_CONNECTION_ATTRIBUTES(Structure):  # hanya bagian awal yang dibutuhkan
    _fields_ = [("isState", c_uint), ("wlanConnectionMode", c_uint),
                ("strProfileName", c_wchar * 256),
                ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES)]


AKM = {1: "802.1X", 2: "PSK", 3: "802.1X", 4: "PSK", 5: "802.1X", 6: "PSK", 8: "SAE",
       9: "SAE", 11: "802.1X", 12: "802.1X", 18: "OWE", 24: "SAE", 25: "SAE"}


def parse_ies(ies, capability):
    """Uraikan Information Elements beacon -> ssid, BSS Load, lebar kanal, keamanan."""
    r = {"ssid": "", "sta_count": None, "ch_util_pct": None, "width": 20,
         "security": None, "pmf": "no"}
    i, akms, wpa1 = 0, set(), False
    while i + 2 <= len(ies):
        eid, ln = ies[i], ies[i + 1]
        d = ies[i + 2:i + 2 + ln]
        i += 2 + ln
        if len(d) < ln:
            break
        if eid == 0:
            r["ssid"] = d.decode("utf-8", "replace")
        elif eid == 11 and ln >= 3:                      # BSS Load
            r["sta_count"] = d[0] | (d[1] << 8)
            r["ch_util_pct"] = round(d[2] * 100 / 255, 1)
        elif eid == 61 and ln >= 2:                      # HT Operation
            if (d[1] & 0x03) in (1, 3) and r["width"] < 40:
                r["width"] = 40
        elif eid == 192 and ln >= 3:                     # VHT Operation
            if d[0] == 1:
                r["width"] = 160 if d[2] else 80
            elif d[0] in (2, 3):
                r["width"] = 160
        elif eid == 48 and ln >= 8:                      # RSN
            p = 6
            npair = d[p] | (d[p + 1] << 8)
            p += 2 + 4 * npair
            if p + 2 <= ln:
                nakm = d[p] | (d[p + 1] << 8)
                p += 2
                for k in range(nakm):
                    if p + 4 * k + 4 <= ln:
                        akms.add(AKM.get(d[p + 4 * k + 3], "other"))
                p += 4 * nakm
                if p + 2 <= ln:
                    cap = d[p] | (d[p + 1] << 8)
                    r["pmf"] = "required" if cap & 0x40 else ("capable" if cap & 0x80 else "no")
        elif eid == 221 and d[:4] == bytes([0x00, 0x50, 0xF2, 0x01]):
            wpa1 = True
    if akms:
        if "SAE" in akms and "PSK" in akms:
            r["security"] = "WPA2/WPA3-Personal"
        elif "SAE" in akms:
            r["security"] = "WPA3-Personal"
        elif "802.1X" in akms:
            r["security"] = "WPA2/3-Enterprise"
        elif "OWE" in akms:
            r["security"] = "OWE"
        elif "PSK" in akms:
            r["security"] = "WPA2-Personal"
        else:
            r["security"] = "RSN-other"
    elif wpa1:
        r["security"] = "WPA(legacy)"
    else:
        r["security"] = "WEP" if capability & 0x0010 else "Open"
    return r


def _mac(b):
    return ":".join("%02x" % x for x in b)


class _Wlan:
    def __init__(self):
        self.api = ctypes.windll.wlanapi
        self.h = c_void_p()
        ver = c_uint32()
        self._ok(self.api.WlanOpenHandle(2, None, byref(ver), byref(self.h)), "WlanOpenHandle")
        lst = POINTER(WLAN_INTERFACE_INFO_LIST)()
        self._ok(self.api.WlanEnumInterfaces(self.h, None, byref(lst)), "WlanEnumInterfaces")
        n = lst.contents.dwNumberOfItems
        if n == 0:
            raise RuntimeError("Tidak ada adaptor Wi-Fi")
        arr = cast(lst.contents.InterfaceInfo, POINTER(WLAN_INTERFACE_INFO * n)).contents
        self.guid = GUID()
        ctypes.memmove(byref(self.guid), byref(arr[0].InterfaceGuid), sizeof(GUID))
        self.desc = arr[0].strInterfaceDescription
        self.api.WlanFreeMemory(lst)

    @staticmethod
    def _ok(rc, fn):
        if rc == 5:
            raise PermissionError("%s: akses ditolak. Aktifkan izin Location untuk desktop apps "
                                  "(Settings > Privacy & security > Location)." % fn)
        if rc != 0:
            raise OSError("%s gagal, kode %d" % (fn, rc))

    def scan(self, wait=4.0):
        self._ok(self.api.WlanScan(self.h, byref(self.guid), None, None, None), "WlanScan")
        time.sleep(wait)  # driver wajib menyelesaikan scan dalam 4 detik
        bl = POINTER(WLAN_BSS_LIST)()
        self._ok(self.api.WlanGetNetworkBssList(self.h, byref(self.guid), None, 3, 0, None,
                                                byref(bl)), "WlanGetNetworkBssList")
        n = bl.contents.dwNumberOfItems
        base = ctypes.addressof(bl.contents.wlanBssEntries)
        aps = []
        for k in range(n):
            addr = base + k * sizeof(WLAN_BSS_ENTRY)
            e = WLAN_BSS_ENTRY.from_address(addr)
            ies = ctypes.string_at(addr + e.ulIeOffset, e.ulIeSize)
            ap = parse_ies(ies, e.usCapabilityInformation)
            if not ap["ssid"]:
                ap["ssid"] = bytes(e.dot11Ssid.ucSSID[:e.dot11Ssid.uSSIDLength]).decode("utf-8", "replace")
            ap.update(bssid=_mac(e.dot11Bssid), freq=e.ulChCenterFrequency // 1000,
                      rssi=float(e.lRssi))
            aps.append(ap)
        self.api.WlanFreeMemory(bl)
        return aps

    def link_quick(self):
        """BSSID + kualitas sinyal (0-100%) tanpa scan, untuk log roaming yang cepat."""
        size, data, vt = c_uint32(), c_void_p(), c_uint()
        if self.api.WlanQueryInterface(self.h, byref(self.guid), 7, None, byref(size),
                                       byref(data), byref(vt)) != 0:
            return "", None
        aa = cast(data, POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents.wlanAssociationAttributes
        res = (_mac(aa.dot11Bssid), aa.wlanSignalQuality)
        self.api.WlanFreeMemory(data)
        return res

    def link(self):
        size, data, vt = c_uint32(), c_void_p(), c_uint()
        rc = self.api.WlanQueryInterface(self.h, byref(self.guid), 7, None, byref(size),
                                         byref(data), byref(vt))
        if rc != 0:
            return {"bssid": "", "freq": "", "rssi": "", "tx": ""}
        ca = cast(data, POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
        bssid = _mac(ca.wlanAssociationAttributes.dot11Bssid)
        tx = ca.wlanAssociationAttributes.ulTxRate / 1000.0
        self.api.WlanFreeMemory(data)
        freq = rssi = ""
        for ap in self.scan(wait=2.0):  # RSSI & frekuensi dari BSS yang terasosiasi
            if ap["bssid"] == bssid:
                freq, rssi = ap["freq"], int(ap["rssi"])
        return {"bssid": bssid, "freq": freq, "rssi": rssi, "tx": tx}


_inst = None


def _get():
    global _inst
    if _inst is None:
        _inst = _Wlan()
    return _inst


def scan():
    return _get().scan()


def link():
    return _get().link()


def link_quick():
    return _get().link_quick()


def interface_name():
    return _get().desc
