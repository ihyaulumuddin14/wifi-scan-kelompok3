// WifiScanMac - pembantu scan Wi-Fi untuk macOS (dipanggil oleh wlan_mac.py).
// macOS hanya memberi SSID/BSSID kepada aplikasi (app bundle) yang punya izin Location
// Services, sehingga scan dilakukan di sini lalu hasilnya ditulis ke file JSON.
//
// Pemakaian: open -W -n WifiScanMac.app --args <out.json> [--link]
import AppKit
import CoreLocation
import CoreWLAN
import Foundation

final class Pemindai: NSObject, CLLocationManagerDelegate {
    let lokasi = CLLocationManager()
    let keluaran: String
    let hanyaLink: Bool
    var selesai = false

    init(keluaran: String, hanyaLink: Bool) {
        self.keluaran = keluaran
        self.hanyaLink = hanyaLink
        super.init()
        lokasi.delegate = self
    }

    func mulai() {
        cek(lokasi.authorizationStatus)
        // batas waktu bila pengguna tidak menjawab dialog izin
        DispatchQueue.main.asyncAfter(deadline: .now() + 300) {
            self.tulis(["error": "timeout menunggu izin lokasi"]); exit(5)
        }
    }

    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) { cek(m.authorizationStatus) }

    func cek(_ s: CLAuthorizationStatus) {
        switch s {
        case .authorizedAlways: pindai()
        case .notDetermined: lokasi.requestWhenInUseAuthorization()
        default: tulis(["error": "izin lokasi ditolak (System Settings > Privacy & Security > Location Services > WifiScanMac)"]); exit(2)
        }
    }

    func pindai() {
        if selesai { return }
        selesai = true
        guard let i = CWWiFiClient.shared().interface() else { tulis(["error": "tidak ada antarmuka Wi-Fi"]); exit(3) }
        var hasil: [String: Any] = [:]
        let ch = i.wlanChannel()
        hasil["link"] = ["bssid": i.bssid() ?? "", "ssid": i.ssid() ?? "", "rssi": i.rssiValue(),
                         "noise": i.noiseMeasurement(), "tx": i.transmitRate(),
                         "channel": ch?.channelNumber ?? 0, "band": ch?.channelBand.rawValue ?? 0,
                         "width": ch?.channelWidth.rawValue ?? 0]
        if !hanyaLink {
            do {
                let nets = try i.scanForNetworks(withName: nil)
                hasil["networks"] = nets.map { n -> [String: Any] in
                    let c = n.wlanChannel
                    let aman = (0...15).filter { v in
                        CWSecurity(rawValue: v).map { n.supportsSecurity($0) } ?? false }
                    return ["bssid": n.bssid ?? "", "ssid": n.ssid ?? "", "rssi": n.rssiValue,
                            "noise": n.noiseMeasurement, "channel": c?.channelNumber ?? 0,
                            "band": c?.channelBand.rawValue ?? 0, "width": c?.channelWidth.rawValue ?? 0,
                            "security": aman,
                            "ie": n.informationElementData?.base64EncodedString() ?? ""]
                }
            } catch {
                tulis(["error": "scan gagal: \(error.localizedDescription)"]); exit(4)
            }
        }
        tulis(hasil)
        exit(0)
    }

    func tulis(_ o: [String: Any]) {
        if let d = try? JSONSerialization.data(withJSONObject: o) {
            try? d.write(to: URL(fileURLWithPath: keluaran))
        }
    }
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("pemakaian: WifiScanMac <out.json> [--link]\n".data(using: .utf8)!)
    exit(1)
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let p = Pemindai(keluaran: args[1], hanyaLink: args.contains("--link"))
p.mulai()
app.run()
