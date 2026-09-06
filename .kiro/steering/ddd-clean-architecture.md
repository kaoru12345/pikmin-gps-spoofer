---
inclusion: fileMatch
fileMatchPattern: '*.py'
---

# DDD / Clean Architecture 規約（第一版）

這份文件是本專案的「架構護欄」。任何人（包括 AI）在寫或改 Python 程式碼時，
都要遵守這裡的規則。目的：讓核心業務邏輯乾淨、可測試、不被外部細節綁死。

> 這是實驗版，會持續調整。規則若跟現實衝突，先在對話裡討論再改，不要默默繞過。

---

## 一、核心思想（先懂為什麼，再看規則）

1. **核心邏輯不依賴外部細節。**
   路徑計算、導航決策這種「業務規則」，不准 import Tkinter（GUI）、
   不准 import pymobiledevice3（iPhone）、不准直接讀寫檔案。
   為什麼：這樣核心才能單獨測試、換 GUI 或換裝置時核心一行都不用改。

2. **依賴方向永遠由外往內。**
   外層（GUI、裝置、檔案）可以依賴內層（業務邏輯），內層絕不反過來依賴外層。

3. **可以被抽換的東西，都躲在介面（Port）後面。**
   iPhone GPS、檔案儲存、地圖 API 這些「外部世界」，
   核心只認一份「約定」（介面），不認實際實作。測試時塞假的，上線時塞真的。

---

## 二、分層結構（本專案的目標架構）

由內到外四層。放檔案時照這個分：

```
domain/          ← 最核心。純業務邏輯與領域物件，零外部依賴
  entity/        ← 領域物件：Route、Waypoint、Location、NavigationPlan
  service/       ← 純領域運算：Haversine 距離、路徑插值、防封號抖動
  event/         ← 領域事件（若需要）：NavigationStarted、RouteCompleted

usecase/         ← 業務流程編排。呼叫 domain，透過 port 存取外部
  port/          ← 介面（約定）
    GpsDevice        ← 「能設定 GPS 座標的東西」
    LocationRepository ← 「能存取收藏地點的東西」
    RouteRepository    ← 「能存取收藏路徑的東西」
    RoutingService     ← 「能算出真實道路路徑的東西」
  service/       ← 用例實作：StartNavigationUseCase、TeleportUseCase …

adapter/         ← 轉接器。實作 port，接真實外部世界
  device/        ← iPhoneGpsAdapter（實作 GpsDevice）、FakeGpsDevice（測試用）
  repository/    ← JsonLocationRepository（實作 LocationRepository）
  routing/       ← ValhallaRoutingAdapter、OsrmRoutingAdapter

ui/              ← 最外層。Tkinter 畫面。只負責顯示 + 呼叫 usecase
```

> 現況：目前全部擠在 `app.py`。這是要逐步重構的起點，不是要一次搬完。

---

## 三、依賴方向鐵律（違反就是錯）

| 這一層 | 可以 import | 絕對不准 import |
|--------|------------|----------------|
| `domain/` | 只有標準庫（math、dataclasses…） | usecase、adapter、ui、任何第三方框架 |
| `usecase/` | domain、自己的 port 介面 | adapter 的實作、ui、tkinter、pymobiledevice3 |
| `adapter/` | usecase 的 port、domain、第三方庫 | ui |
| `ui/` | usecase | domain 的內部細節（透過 usecase 存取） |

**最重要的一條：`domain/` 裡不准出現 `import tkinter`、`import pymobiledevice3`、
`import requests`、`open(檔案)`。** 看到就是踩線。

---

## 四、各層的具體規則

### domain/（領域層）
- 領域物件用 `@dataclass` 或純 class，不繼承框架的東西。
- 純運算（Haversine、插值）寫成純函式或無狀態 service：同樣輸入永遠同樣輸出，
  不碰時間、不碰隨機、不碰 I/O（隨機抖動這種「不確定性」要能透過參數注入，方便測試）。
- 領域物件保護自己的規則：外界不能繞過物件直接亂改它的內部狀態。

### usecase/（用例層）
- 一個用例做一件事，類名用動詞：`StartNavigationUseCase`、`SaveLocationUseCase`。
- 用例透過建構子接收它需要的 port（依賴注入），不准自己 new 出實際的 adapter。
  ```python
  # 對：用例只認介面，實作從外面傳進來
  class StartNavigationUseCase:
      def __init__(self, gps: GpsDevice, routing: RoutingService):
          self._gps = gps
          self._routing = routing
  ```
- 用例不碰 GUI、不碰 tkinter widget。

### port/（介面層）
- Python 沒有強制介面，用 `abc.ABC` + `@abstractmethod` 或 `typing.Protocol` 來定義。
  ```python
  from typing import Protocol
  class GpsDevice(Protocol):
      def set_location(self, lat: float, lon: float) -> None: ...
      def clear_location(self) -> None: ...
  ```
- port 定義在 usecase 層，實作放在 adapter 層（依賴反轉的關鍵）。

### adapter/（轉接器層）
- 每個 adapter 實作一個 port。命名：`<技術><Port名>Adapter`，
  例如 `IPhoneGpsAdapter`、`JsonLocationRepository`。
- 每個對外的 port 都要有一個「假的」實作供測試用：`FakeGpsDevice`、
  `InMemoryLocationRepository`。這是能不能寫測試的關鍵。

### ui/（介面層）
- Tkinter 程式碼只做兩件事：畫面、把使用者操作轉成對 usecase 的呼叫。
- 業務判斷（該走多快、路徑怎麼算）不准寫在 UI 的 callback 裡，一律丟給 usecase。

---

## 五、Python 特別注意（沒有編譯器幫你把關）

Python 不會像 Java 那樣阻止你亂 import。所以這裡靠自律：

1. **import 就是最好的檢查工具。** 打開 `domain/` 任一檔案，看 import 區塊——
   只要出現 tkinter / pymobiledevice3 / requests，就是架構被破壞了。
2. 用 `Protocol` 或 `ABC` 明確標示「這是一份約定」，不要用「鴨子型別隨便傳」。
3. 純邏輯優先寫成**純函式**，天生好測、天生無副作用。

---

## 六、TDD 節奏（改行為時照這個走）

1. **紅**：先寫一個描述「我要的結果」的測試，它會失敗（因為還沒實作）。
2. **綠**：寫最少的程式讓測試通過。
3. **重構**：測試保護著你，安心把程式整理乾淨。

- 純邏輯（Haversine、插值）一定要有測試——它們最好測，也最容易寫錯。
- 測試用假的 adapter（FakeGpsDevice），不准在測試裡真的連 iPhone、真的開 GUI。

---

## 七、AI 產出後的自我審查清單

AI（含 Kiro）寫完或改完程式碼後，交付前先逐項自問，並在回覆裡簡短報告：

- [ ] `domain/` 有沒有 import 到 tkinter / pymobiledevice3 / requests / 檔案 I/O？（有就是錯）
- [ ] 依賴方向對嗎？內層有沒有反過來依賴外層？
- [ ] 可抽換的外部（GPS、檔案、地圖 API）有沒有藏在 port 介面後面？
- [ ] 用例有沒有透過建構子接收 port，而不是自己 new adapter？
- [ ] 業務邏輯有沒有洩漏到 UI 的 callback 裡？
- [ ] 純邏輯有沒有對應的測試？測試有沒有用假的 adapter（沒真連硬體 / 開 GUI）？
- [ ] 命名有沒有講出「業務語意」，而不是技術細節？

任何一項打不了勾，就在回覆裡明講哪裡沒符合、為什麼，讓主人決定要不要接受。

---

## 八、不要過度設計

- 這是個人工具，不是企業級系統。分層是為了「好測、好改」，不是為了炫技。
- 只有「會變、會需要抽換、需要測試」的東西才值得抽介面。
  一次性的小工具函式不用硬套四層。
- 有疑慮時，先在對話裡討論邊界怎麼劃，不要悶著頭套規則。
