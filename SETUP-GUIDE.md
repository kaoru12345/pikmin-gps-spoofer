# Pikmin GPS Spoofer — 完整安裝指南

> 給非工程師的一步一步指南。照著做就好，不用懂原理。

---

## 你需要什麼

- Windows 10/11 電腦
- iPhone（iOS 17 以上）
- USB 傳輸線（要能傳資料的，不是純充電線。原廠線最穩）
- 網路連線（安裝 Python 套件需要）

---

## 資料夾裡有什麼

```
pikmin-gps-spoofer/
├── app.py                  ← 主程式
├── requirements.txt        ← Python 套件清單
├── SETUP-GUIDE.md          ← 你在看的這個
├── README.md               ← 技術文件
├── app/
│   ├── iTunes64Setup.exe   ← iTunes 安裝檔（已附）
│   └── VSCodeUserSetup-x64-1.131.0.exe  ← VS Code 安裝檔（已附）
```

---

## Step 1：安裝 VS Code

1. 打開 `app\VSCodeUserSetup-x64-1.131.0.exe`
2. 一路 Next 裝完
3. 打開 VS Code，左邊 Extensions 搜尋「**Gemini**」裝起來（或你慣用的 AI extension）

---

## Step 2：安裝 Python

1. 打開瀏覽器，進入 https://www.python.org/downloads/
2. 點最大的黃色按鈕「Download Python 3.12.x」下載
3. 執行安裝程式
4. **⚠️ 重要：安裝第一個畫面下方「Add python.exe to PATH」→ 一定要打勾！**
5. 點 Install Now，等它跑完

驗證（打開 PowerShell，按 Win 鍵輸入 powershell）：

```
python --version
```

應該顯示 `Python 3.12.x`。

---

## Step 3：安裝 iTunes

1. 打開 `app\iTunes64Setup.exe`
2. 全部用預設選項，一路 Next 裝完
3. **裝完不用打開 iTunes**

驗證（PowerShell）：

```
sc.exe query "Apple Mobile Device Service"
```

應該看到 `STATE: 4 RUNNING`。

---

## Step 4：安裝 Python 套件

1. 打開 PowerShell，輸入 `cd` 指令切換到你的專案資料夾（路徑請改成你自己的）：

```powershell
cd ~\Desktop\pikmin-gps-spoofer
```

> 不需要安裝 Visual Studio Build Tools，不需要編譯。

---

## Step 5：設定 iPhone（需要人操作）

1. 用 USB 線把 iPhone 接到電腦
2. **手機要解鎖、螢幕亮著**
3. 手機上會彈出「**信任此電腦？**」→ 點「信任」→ 輸入手機密碼
4. 打開 iTunes，確認左上角出現手機圖示 📱

> 沒彈信任？→ 換 USB 口（用電腦背面的）或換一條線（原廠線）

---

## Step 6：開啟開發者模式（只需做一次）

```powershell
cd ~\Desktop\pikmin-gps-spoofer
python app.py
```

點按鈕 **「0. 一鍵開啟開發者模式」**，然後在 iPhone：

1. 設定 → 隱私權與安全性 → **開發者模式** → 打開
2. 手機重開機 → 再確認一次「開啟」
3. 重新插 USB 線

> 之後就不用再做了！

---

## Step 7：開始種花

```powershell
cd ~\Desktop\pikmin-gps-spoofer
python app.py
```

1. 在地圖上**左鍵點**設定起點 A（上方選「設定起點 A」）
2. 切換到「設定終點 B」，再點一個位置
3. 點「**1. 抓取道路路徑**」→ 地圖出現藍色路線
4. 點「**2. 開始自動種花**」→ 手機 GPS 開始沿路走
5. 打開 Pikmin Bloom → 角色在走了 🌸
6. 要停就點「**3. 停止移動**」

---

## 建議設定

- 手機「自動鎖定」改成「**永不**」（設定 → 螢幕顯示與亮度 → 自動鎖定）
- Pikmin 種花建議時速 8~12 km/h
- Pokémon GO 建議 ≤ 10 km/h
- GPS Jitter 保持開啟（更擬真）

---

## 給 AI 的自動安裝指令

在 VS Code 裝好 Gemini Extension 後，貼這段給它：

```
請幫我完成 pikmin-gps-spoofer 的環境安裝，照 SETUP-GUIDE.md 做 Step 2~4。
Step 1 (VS Code) 我已經裝了。Step 5~6 需要手動操作 iPhone 我自己來。
請只做軟體安裝和驗證的部分。
```

---

## 常見問題

| 狀況                 | 怎麼辦                           |
| -------------------- | -------------------------------- |
| 手機沒彈信任         | 拔掉 USB 重插，手機解鎖螢幕亮著  |
| iTunes 看不到手機    | 換 USB 線（原廠的）或 USB 口     |
| 程式顯示「測試模式」 | iPhone 沒連上，檢查 USB 和信任   |
| 找不到開發者模式     | 按 app「一鍵開啟開發者模式」按鈕 |
| 走到一半斷了         | USB 接觸不良，換口或線           |
