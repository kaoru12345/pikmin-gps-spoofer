# Pikmin GPS Auto-Navigator

透過 USB 控制 iPhone GPS 座標，自動沿真實道路行走。主要用於 Pikmin Bloom 種花與 Pokémon GO。

支援 **iOS 17+ / iOS 26**（CoreDevice Tunnel + DVT LocationSimulation API）。

## 功能一覽

| 功能 | 說明 |
|------|------|
| 🗺 互動地圖 | 嵌入式 Google Maps，左鍵點擊設定 A/B/C 點，右鍵選單，自動交替 |
| ⚡ 瞬移 | 一鍵瞬移到指定座標，原地微飄移模擬真實 GPS |
| 🌸 自動種花 | 沿真實道路路徑自動移動，速度可即時調整 |
| 🌀 繞圈種花 | 以 A 點為圓心，螺旋狀擴大繞圈 |
| 🌈 閃爍模式 | 撿活動彩虹盆栽用，短暫釋放 GPS 讓遊戲載入物件 |
| 📱 螢幕投影 | 即時顯示 iPhone 畫面（置頂視窗，可縮放） |
| 🔍 搜尋地點 | Nominatim 地名搜尋，輸入地名直接定位 |
| 📋 貼上座標 | 支援蘑菇地圖格式 `lat, lon` 直接貼上 |
| 🗺 路徑規劃 | Valhalla / OSRM 真實道路路徑（步行/腳踏車/開車） |
| ⭐ 收藏管理 | 儲存/載入常用地點與路徑 |
| 🌙 深色模式 | 護眼 |
| 🔧 一鍵開發者模式 | 自動觸發 iPhone 開發者模式選項 |
| 🏷 版號顯示 | 標題列顯示目前 git tag 版本 |

## 防封號機制

- Haversine 平滑插值，每秒微量移動
- 時速 ±1.5 km/h 隨機波動模擬人類行為
- 高斯 GPS Jitter（σ=0.000008°）模擬衛星飄移
- 走真實道路折線，不切過建築

## 系統需求

- Windows 10/11
- Python 3.10+
- **iTunes 桌面版**（[apple.com/itunes](https://www.apple.com/itunes/download/win64)，非 Microsoft Store 版）
- iPhone 已開啟**開發者模式**
- USB 資料線（非純充電線）

## 安裝

```bash
# 1. Clone
git clone https://github.com/kaoru12345/pikmin-gps-spoofer.git
cd pikmin-gps-spoofer

# 2. 安裝套件
pip install requests tkintermapview Pillow
pip install pymobiledevice3 --no-deps
pip install construct construct-typing bpylist2 pyusb parameter-decorators pycrashreport fastapi uvicorn wsproto inquirer3 ifaddr hyperframe srptools qh3 developer_disk_image opack2 psutil pytun-pmd3 prompt_toolkit python-pcapng plumbum pyiosbackup typer typer-injector defusedxml pywin32 pmd-pytcp coloredlogs arrow pycryptodome pylzss asn1 pykdebugparser tqdm cryptography certifi

# 3. 啟動
python app.py
```

> 如果你的環境能正常編譯 C 擴充，可以直接 `pip install -r requirements.txt`

## iPhone 前置準備

1. 安裝 **iTunes 桌面版**（Win64），確認 `Apple Mobile Device Service` 在 services.msc 中 Running
2. USB 連接 iPhone，手機上點「**信任此電腦**」
3. 開啟**開發者模式**：設定 → 隱私權與安全性 → 開發者模式 → 打開
   - 找不到選項？app 裡按「一鍵開啟開發者模式」觸發顯示

## 使用流程

1. 在地圖上點擊設定起點 A、經過點 B、終點 C
2. 設定目標時速（建議種花 8~12 km/h）
3. 點「抓取道路路徑」→ 地圖上畫出藍色路線
4. 點「開始自動種花」→ 每秒注入 GPS，沿路線移動
5. 「停止移動」可隨時中斷所有移動

## 撿活動盆栽（閃爍模式）

1. 設定 A 點為盆栽座標
2. 瞬移到 A 點
3. 打開 Pikmin Bloom
4. 按「🌈 閃爍模式」
5. 釋放 GPS 的 3 秒內趕快撿盆栽

## 技術架構

```
┌─────────────┐     USB      ┌──────────────────┐
│  Windows PC │◄────────────►│     iPhone       │
│             │              │                  │
│  app.py     │   Tunnel     │  CoreDevice      │
│  ├─ OSRM   │◄────────────►│  DVT Service     │
│  ├─ Map UI  │   (TCP/QUIC) │  LocationSim     │
│  └─ Engine  │              │  Screenshot      │
└─────────────┘              └──────────────────┘
```

- **pymobiledevice3** → USB tunnel (UserspaceRsdTunnel)
- **DVT LocationSimulation** → GPS 座標注入
- **DVT Screenshot** → 螢幕投影
- **Valhalla / OSRM** → 免費道路路徑規劃
- **tkintermapview** → 嵌入式 Google Maps 瓦片地圖

## 測試模式

未連接 iPhone 時自動進入測試模式，地圖、路徑規劃、日誌全部正常運作，只是不注入 GPS。

## 更新

```bash
git pull
```

或雙擊 `update.bat`。

## 發版（開發者）

```bash
git add .
git commit -m "描述"
git push
git tag v1.x.x
git push origin v1.x.x
```

推 tag 後 GitHub Actions 自動打包 exe 到 [Releases](https://github.com/kaoru12345/pikmin-gps-spoofer/releases) 頁面。

## 疑難排解

| 問題 | 解法 |
|------|------|
| `NoDeviceConnectedError` | 拔掉重插 USB，手機解鎖，確認信任 |
| `InvalidService` | 開發者模式沒開 |
| iTunes 看不到手機 | 換 USB 口（主機板直連）、換資料線 |
| `ConnectionFailedToUsbmuxdError` | Apple Mobile Device Service 沒跑，services.msc 重啟 |
| `userspace tunnel already active` | 關掉 app 重開（一個 process 只能一個 tunnel） |
