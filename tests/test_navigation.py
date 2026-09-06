"""
domain.service.navigation 的測試。

重點示範：把「隨機」抽成可注入的 noise 來源後，原本無法測試的插值邏輯，
現在可以透過注入「可預測的假 noise」來精確斷言結果。
"""
from domain.service.navigation import interpolate_points


class ZeroNoise:
    """零雜訊來源：速度不波動、座標不抖動。讓輸出完全可預測。"""

    def speed_fluctuation(self) -> float:
        return 0.0

    def position_jitter(self) -> float:
        return 0.0


class FixedJitter:
    """固定抖動來源：速度不波動，但每軸座標固定偏移一個量。"""

    def __init__(self, jitter):
        self._jitter = jitter

    def speed_fluctuation(self) -> float:
        return 0.0

    def position_jitter(self) -> float:
        return self._jitter


def test_零雜訊時第一個點就是起點():
    coords = [(0.0, 0.0), (0.0, 0.01)]
    points = list(interpolate_points(coords, speed_mps=5.0, jitter_enabled=False, noise=ZeroNoise()))
    # 第一步 t=0，應該剛好落在起點
    first_lat, first_lon, _ = points[0]
    assert first_lat == 0.0
    assert first_lon == 0.0


def test_終點一定會被吐出且速度為零():
    coords = [(0.0, 0.0), (0.0, 0.01)]
    points = list(interpolate_points(coords, speed_mps=5.0, jitter_enabled=False, noise=ZeroNoise()))
    last_lat, last_lon, last_speed = points[-1]
    assert (last_lat, last_lon) == (0.0, 0.01)
    assert last_speed == 0.0


def test_關閉jitter時座標不受抖動影響():
    # 就算 noise 會給抖動，jitter_enabled=False 時也不該套用
    coords = [(0.0, 0.0), (0.0, 0.01)]
    points = list(interpolate_points(coords, speed_mps=5.0, jitter_enabled=False,
                                     noise=FixedJitter(0.001)))
    # 每一步的座標都應該落在直線上（lat 恆為 0），沒有被抖動污染
    for lat, lon, _ in points:
        assert lat == 0.0


def test_開啟jitter時座標會被固定偏移():
    coords = [(0.0, 0.0), (0.0, 0.01)]
    points = list(interpolate_points(coords, speed_mps=5.0, jitter_enabled=True,
                                     noise=FixedJitter(0.001)))
    # 開啟抖動後，中間點的 lat 應該被偏移 0.001（不再恆為 0）
    # 取第一個點驗證：起點 lat=0 加上固定偏移 0.001
    first_lat, _, _ = points[0]
    assert first_lat == 0.001


def test_空路徑不產生任何點():
    assert list(interpolate_points([], speed_mps=5.0, jitter_enabled=False, noise=ZeroNoise())) == []
