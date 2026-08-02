# Pikmin/PokéGO GPS Auto-Navigator

透過 USB 控制 iPhone GPS 座標，自動沿真實道路行走，用於 Pikmin Bloom 種花與 Pokémon GO。

支援 **iOS 17+ / iOS 26**（使用 CoreDevice Tunnel + DVT LocationSimulation API）。

## 系統需求

- Windows 10/11
- Python 3.10+
- **iTunes 桌面版**（從 [apple.com/itunes/download/win64](https://www.apple.com/itunes/download/win64) 下載，不是 Microsoft Store 版）
- iPhone 已開啟**開發者模式**

## 安裝

```bash
cd Bob\pikmin-gps-spoofer
pip install -r requirements.txt
```

如果 `lzfse` 編譯失敗（Python 3.13），可以跳過它：

```bash
pip install requests tkintermapview
pip install pymobiledevice3 --no-deps
# 再手動裝其他依賴（見 requirements.txt 備註）
```

GPS 模擬功能不需要 `lzfse`/`pyimg4`/`ipsw_parser`，那些是固件解析用的。

## iPhone 前置準備

1. 安裝 **iTunes 桌面版**（Win64），確認 `Apple Mobile Device Service` 在 services.msc 中顯示 Running
2. USB 連接 iPhone，手機上點「**信任此電腦**」
3. 開啟**開發者模式**：
   - 如果設定裡找不到，先用 pymobiledevice3 觸發顯示：
     ```bash
     python -c "
     import asyncio
     from pymobiledevice3.lockdown import create_using_usbmux
     from pymobiledevice3.services.amfi import AmfiService
     async def main():
         lockdown = await create_using_usbmux(autopair=True)
         amfi = AmfiService(lockdown)
         await amfi.reveal_developer_mode_option_in_ui()
         print('Done! Go to Settings -> Privacy & Security -> Developer Mode')
     asyncio.run(main())
     "
     ```
   - 然後去 **設定 → 隱私權與安全性 → 開發者模式** → 打開 → 重開機 → 確認開啟

## 啟動

```bash
python app.py
```

## 使用流程

1. 在地圖上**左鍵點擊**設定起點 A（綠色）和終點 B（紅色），或右鍵選單選取
2. 設定目標時速（建議 Pikmin 種花 8~12 km/h）
3. 點「**抓取道路路徑**」→ 呼叫 OSRM 取得真實道路節點，地圖上畫出藍色路線
4. 點「**開始自動種花**」→ 建立 CoreDevice Tunnel，每秒注入一次 GPS
5. 隨時點「**停止移動**」中斷，自動恢復真實 GPS

## 技術架構

```
┌─────────────┐     USB      ┌──────────────────┐
│  Windows PC │◄────────────►│     iPhone       │
│             │              │                  │
│  app.py     │   Tunnel     │  CoreDevice      │
│  ├─ OSRM   │◄────────────►│  DVT Service     │
│  ├─ Map UI  │   (TCP/QUIC) │  LocationSim     │
│  └─ Engine  │              │                  │
└─────────────┘              └──────────────────┘
```

- **pymobiledevice3** → USB tunnel via `UserspaceRsdTunnel`
- **DVT LocationSimulation** → `simulateLocationWithLatitude:longitude:` (Instruments channel)
- **OSRM API** → 免費道路路徑規劃
- **tkintermapview** → 嵌入式 Google Maps 瓦片地圖

## 防封號機制

- Haversine 平滑插值，每秒微量移動不跳點
- 時速 ±1.5 km/h 隨機波動模擬人類行為
- 可開啟高斯 GPS Jitter（σ=0.000008°）模擬衛星飄移
- 走 OSRM 真實道路折線，不切過建築

## 測試模式

未連接 iPhone 時自動進入測試模式，地圖、路徑規劃、模擬走路日誌全部正常運作，只是不注入 GPS。

## 疑難排解

| 問題                             | 解法                                                    |
| -------------------------------- | ------------------------------------------------------- |
| `NoDeviceConnectedError`         | 拔掉重插 USB，手機解鎖狀態，確認信任                    |
| `InvalidService`                 | 開發者模式沒開，見前置準備步驟 3                        |
| iTunes 看不到手機                | 換 USB 口（用主機板直連的）、換線（需資料線非純充電線） |
| 關機卡很久                       | 裝置管理員清除 Apple 幽靈裝置                           |
| `ConnectionFailedToUsbmuxdError` | Apple Mobile Device Service 沒在跑，services.msc 重啟它 |
