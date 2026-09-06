# 皮克敏 DDD / Clean Architecture 重構進度

這份是重構的「進度書籤」。隔幾天回來、或換人接手時，先讀這份就知道走到哪、接下來做什麼。
搭配 `.kiro/steering/ddd-clean-architecture.md`（架構規約）一起看。

---

## 為什麼做這個

拿皮克敏這個個人專案，把 2000 行的 `app.py`（GUI + 邏輯 + 硬體 + 檔案全混在一起）
逐步重構成分層清楚、核心可測試的 Clean Architecture 結構。

真正目的不是「重構完美」，而是：
1. 把課堂學的 DDD / Clean Architecture / TDD 從「聽懂」變「做得出來」
2. 練架構師的判斷力（邊界劃哪、什麼該抽什麼不該抽）
3. 練「怎麼調教 AI 產出符合架構的程式」（靠 steering 規約 + 審查清單）

---

## 分支策略

- `main`：穩定，只含 Step 1，**不要在上面做重構**。tag `v2.12`。
- `refactor/ddd-clean-arch`：所有重構都在這條做。做到滿意再合併回 main。

目前在 `refactor/ddd-clean-arch`。

---

## 進度

### ✅ Step 1：haversine → domain（commit 已在 main + tag v2.12）
- 建 `domain/service/geo.py`，haversine 純運算移入
- `tests/test_geo.py`（4 測試）
- app.py 改 import

### ✅ Step 2：interpolate_points → domain（commit 8fb6398, cf81908）
- 建 `domain/service/navigation.py`：插值邏輯 + `MovementNoise` 介面 + `RealMovementNoise`
- 隨機性（速度波動、GPS 抖動）抽成可注入的 `MovementNoise`，測試可注入假 noise 精確斷言
- 收斂重複的 magic number：`speed_fluctuation_kmh()` / `position_jitter_degrees()` 成單一來源
- app.py 6 處重複的 random 呼叫改用 domain 函式
- `tests/test_navigation.py`（5）、`tests/test_noise_helpers.py`（4）
- **重要判斷**：app.py 有三種「移動流程」（獨立函式版 generator、繞圈狀態機、即時導航狀態機），
  結構不同，只收斂共用的小計算，**沒有合併流程**（硬合併會改行為、弄壞暫停/重連）

**目前測試：13 個全綠**（`python -m pytest tests/ -q`，用 JDK 無關，Python 3.13 + pytest）

---

## ⬜ 還沒做（接下來）

### Step 3：抽 GpsDevice Port + FakeGpsDevice（建議下一步）
- 把 `iPhoneGPS` 藏到 `GpsDevice` 介面後面（見規約分層：usecase/port）
- 做一個 `FakeGpsDevice` 供測試
- 目的：導航流程可以不接 iPhone 就測試（依賴反轉核心）
- 難度：中

### Step 4：load/save → Repository
- `load_locations` / `save_locations` / `load_routes` / `save_routes` 收進
  `LocationRepository` / `RouteRepository`
- 難度：低

### Step 5：導航流程 → Use Case（最大、最難，可選）
- 把繞圈、即時導航那兩段纏在 GUI 執行緒裡的流程抽成 usecase
- GUI 只剩畫面 + 呼叫 usecase
- 牽涉 GUI 執行緒、狀態控制（_running/_paused）、斷線重連 → 最需小心，可能再拆多步
- 報酬遞減，個人工具不一定要做到底

---

## 建議收斂點

走完 Step 3、4 就是漂亮的段落：domain 紮實、外部都藏在介面後、可測試性大幅提升。
Step 5 看興致與時間再決定。

---

## 回來接手怎麼開始

1. 確認在對的分支：`git checkout refactor/ddd-clean-arch`
2. 跑測試確認綠：`python -m pytest tests/ -q`（應 13 passed）
3. 跟 Kiro 說「繼續 Step 3」即可

## 重構鐵律（每步都遵守）

- 先看懂現況再改，不憑印象
- 小步前進，每步驗證（語法 OK + 測試綠）
- 不改變外部行為（重構 ≠ 改功能）
- 表面相似 ≠ 本質相同：別硬合併不同的東西
- 動 git 前先問主人；commit 指定檔案不用 `git add .`
