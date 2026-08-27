# Pikmin GPS Auto-Navigator

透過 USB 控制 iPhone GPS 座標，自動沿真實道路行走。主要用於 Pikmin Bloom 種花與 Pokémon GO。

支援 **iOS 17+ / iOS 26**（CoreDevice Tunnel + DVT LocationSimulation API）。

## 功能一覽

| 功能              | 說明                                                                              |
| ----------------- | --------------------------------------------------------------------------------- |
| 🗺 互動地圖       | 嵌入式 Google Maps，左鍵點擊設定 A/B/C 點，右鍵選單                               |
| ⚡ 瞬移           | 一鍵瞬移到指定座標，原地微飄移模擬真實 GPS                                        |
| 🌸 自動種花       | 沿真實道路路徑自動移動，速度可即時調整                                            |
| 🌀 繞圈種花       | 以 A 點為圓心，螺旋狀擴大繞圈                                                     |
| 🕹 方向控制盤     | 8 方向 D-pad + 鍵盤 WASD，手動微調 GPS 位置                                       |
| ✏ 手繪路徑        | 在地圖上點擊或拖曳畫出路線，自動對齊道路                                          |
| 🌈 閃爍模式       | 撿活動彩虹盆栽用，短暫釋放 GPS 讓遊戲載入物件                                     |
| 📱 螢幕投影       | 即時顯示 iPhone 畫面（置頂視窗，可縮放）                                          |
| 🔍 搜尋地點       | Nominatim 地名搜尋（支援中文，Photon 備援），輸入地名直接定位                     |
| 📋 貼上座標       | 支援蘑菇地圖格式 `lat, lon` 直接貼上                                              |
| 🗺 路徑規劃       | Valhalla / OSRM 真實道路路徑（步行/腳踏車/開車）                                  |
| ⭐ 收藏管理       | 儲存/載入常用地點與路徑，分類篩選（純點/菇點/明信片點/我的最愛/活動點），可改分類 |
| 📤 匯出/匯入      | 將收藏地點與路徑匯出為 JSON，方便備份與分享                                       |
| 🌙 深色模式       | sv_ttk Win11 風格淺/深主題切換，日誌配色同步                                      |
| 🔧 一鍵開發者模式 | 自動觸發 iPhone 開發者模式選項                                                    |
| 🎨 彩色 Icon      | 全介面 Pillow 動態彩色圖示（非黑白 emoji）                                        |
| ⏱ 導航剩餘時間    | 完整模式與迷你模式即時顯示 ETA 和進度                                             |
| 📦 自動安裝依賴   | 首次執行自動安裝缺少的 Python 套件                                                |

## 介面分頁

右側控制面板使用 Tab 分頁組織功能：

| Tab         | 內容                                                                                           |
| ----------- | ---------------------------------------------------------------------------------------------- |
| 🗺 導航     | 搜尋地點、貼上座標、路徑設定 (A/B/C)、速度/路線模式、瞬移、抓取路徑、開始/停止種花、繞圈、暫停 |
| 🕹 方向控制 | 步距設定 (2m~50m)、3×3 方向鍵、快捷鍵提示                                                      |
| 🔧 工具     | 開發者模式、閃爍模式、手機投影                                                                 |
| ✏ 手繪路徑  | 點擊加點 / 拖曳畫線、自動對齊道路、生成路徑                                                    |

## 迷你模式

按「迷你模式」按鈕可切換為小型置頂浮動視窗：

- 顯示目前狀態（種花中 / 暫停 / 停留飄動 / 閒置）
- 即時 ETA 剩餘時間（格式：`~30秒` 或 `~5.2分`）
- 暫停/停止/還原完整模式；暫停鍵按下會變綠色「繼續」
- 尚未開始移動時，暫停與停止鍵為停用狀態
- 可拖曳移動位置

## 方向控制盤

在「🕹 方向控制」tab 中，提供 3×3 的方向鍵 grid：

- 按方向鍵每次偏移設定的步距公尺數
- 中間「回A」按鈕回到 A 點座標
- 支援鍵盤快捷鍵（焦點不在輸入框時）：
  - `W/A/S/D` 或方向鍵 = 上/左/下/右
  - `Q/E/Z/C` = 左上/右上/左下/右下
- 瞬移後按方向鍵會停止飄動，切換為手動控制

## 手繪路徑

在「✏ 手繪路徑」tab 中：

**點擊加點模式：**

1. 按「開始畫路徑」
2. 在地圖上逐一點擊添加路徑點（紫色標記）
3. 按「生成路徑」

**拖曳畫線模式：**

1. 選擇「拖曳畫線」
2. 按「開始畫路徑」
3. 在地圖上按住左鍵拖曳畫出路線
4. 放開滑鼠完成一段，可繼續拖曳延伸
5. 按「生成路徑」

- 「自動對齊道路」開啟時，會透過 Valhalla/OSRM 把手繪點對齊到真實道路
- 拖曳畫線產生的節點過多時，送 API 前會自動精簡到安全數量（最多 40 個 waypoint），避免請求失敗
- 支援撤回上一點、清除所有點、清除已生成路徑

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
git clone https://github.com/kaoru12345/pikmin-gps-spoofer.git
cd pikmin-gps-spoofer

pip install requests tkintermapview Pillow sv_ttk
pip install pymobiledevice3 --no-deps
pip install construct construct-typing bpylist2 pyusb parameter-decorators pycrashreport fastapi uvicorn wsproto inquirer3 ifaddr hyperframe srptools qh3 developer_disk_image opack2 psutil pytun-pmd3 prompt_toolkit python-pcapng plumbum pyiosbackup typer typer-injector defusedxml pywin32 pmd-pytcp coloredlogs arrow pycryptodome pylzss asn1 pykdebugparser tqdm cryptography certifi

python app.py
```

> **提示：** 首次執行 `python app.py` 時，程式會自動檢查並安裝缺少的基本套件（requests、tkintermapview、Pillow、sv_ttk）。pymobiledevice3 及其相依套件仍需手動安裝。

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
5. 導航進行中會顯示剩餘時間和進度百分比
6. 「停止」可隨時中斷所有移動

## 撿活動盆栽（閃爍模式）

1. 設定 A 點為盆栽座標
2. 瞬移到 A 點
3. 打開 Pikmin Bloom
4. 按「閃爍模式」
5. 釋放 GPS 的 3 秒內趕快撿盆栽

## 發版

```bash
git tag v2.x.x
git push origin v2.x.x
```

推 tag 後 GitHub Actions 自動打包 exe 到 [Releases](https://github.com/kaoru12345/pikmin-gps-spoofer/releases)。

## 疑難排解

| 問題                              | 解法                                                |
| --------------------------------- | --------------------------------------------------- |
| `NoDeviceConnectedError`          | 拔掉重插 USB，手機解鎖，確認信任                    |
| `InvalidService`                  | 開發者模式沒開                                      |
| iTunes 看不到手機                 | 換 USB 口（主機板直連）、換資料線                   |
| `ConnectionFailedToUsbmuxdError`  | Apple Mobile Device Service 沒跑，services.msc 重啟 |
| `userspace tunnel already active` | 關掉 app 重開                                       |
