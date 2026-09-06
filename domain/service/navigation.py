"""
路徑導航的純運算 —— 領域層 (domain) 的一部分。

核心是 interpolate_points：把一串路徑點，內插成「每秒該在的 GPS 座標」，
模擬真人沿路移動（含速度波動與 GPS 抖動的防封號效果）。

設計重點（見 .kiro/steering/ddd-clean-architecture.md 第四、五段）：
「不確定性 (隨機) 透過參數注入」。本模組不直接呼叫 random，而是向外部索取一個
MovementNoise 來源：
  - 正式跑：注入 RealMovementNoise（內部用 random，行為與重構前完全一致）
  - 測試：注入一個可預測的假來源（例如永遠回 0），讓輸出可被精確斷言

依賴規則：本檔只 import 標準庫，不得 import tkinter / pymobiledevice3 / requests。
"""
import random
from typing import Protocol

from domain.service.geo import haversine


class MovementNoise(Protocol):
    """移動時的雜訊來源（速度波動、GPS 抖動）。這是一份約定 (Port)。"""

    def speed_fluctuation(self) -> float:
        """回傳速度波動量（單位：公尺/秒）。"""
        ...

    def position_jitter(self) -> float:
        """回傳單一座標軸（緯或經）的抖動量（單位：度）。"""
        ...


class RealMovementNoise:
    """正式用的雜訊來源，行為與重構前的 random 呼叫完全相同。"""

    def speed_fluctuation(self) -> float:
        # 原本：random.uniform(-1.5, 1.5) / 3.6  （±1.5 km/h 換算成 m/s）
        return random.uniform(-1.5, 1.5) / 3.6

    def position_jitter(self) -> float:
        # 原本：random.gauss(0, 0.000008)  （GPS 衛星飄移）
        return random.gauss(0, 0.000008)


def interpolate_points(coords, speed_mps, jitter_enabled, noise=None):
    """
    Given a list of (lat, lon) waypoints and a base speed (m/s),
    yield interpolated (lat, lon) at ~1 Hz with speed fluctuation and optional jitter.

    noise: MovementNoise 來源。省略時預設用 RealMovementNoise，
           因此舊呼叫端（不傳 noise）行為完全不變。
    """
    if noise is None:
        noise = RealMovementNoise()

    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        seg_dist = haversine(lat1, lon1, lat2, lon2)
        if seg_dist < 0.01:
            continue

        fluctuation = noise.speed_fluctuation()
        actual_speed = max(0.5, speed_mps + fluctuation)

        steps = max(1, int(seg_dist / actual_speed))
        for s in range(steps):
            t = s / steps
            lat = lat1 + (lat2 - lat1) * t
            lon = lon1 + (lon2 - lon1) * t

            if jitter_enabled:
                lat += noise.position_jitter()
                lon += noise.position_jitter()

            fluctuation = noise.speed_fluctuation()
            actual_speed = max(0.5, speed_mps + fluctuation)

            yield lat, lon, actual_speed * 3.6

    if coords:
        lat, lon = coords[-1]
        if jitter_enabled:
            lat += noise.position_jitter()
            lon += noise.position_jitter()
        yield lat, lon, 0.0
