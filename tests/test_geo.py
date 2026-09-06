"""
domain.service.geo 的測試。

這是本專案第一個「純領域邏輯」測試 —— 不碰 GUI、不碰 iPhone、不碰檔案，
只驗證數學運算對不對。這正是 Clean Architecture 想要的：核心邏輯能單獨測試。
"""
import math

from domain.service.geo import haversine


def test_同一點距離為零():
    # 一個點到它自己，距離必為 0
    assert haversine(25.033, 121.565, 25.033, 121.565) == 0


def test_已知兩點距離接近真實值():
    # 台北 101 (25.0339, 121.5645) 到 台北車站 (25.0478, 121.5170)
    # 實際直線距離約 5 公里左右。我們容許 ±300 公尺誤差。
    dist = haversine(25.0339, 121.5645, 25.0478, 121.5170)
    assert 4700 < dist < 5300


def test_距離具對稱性():
    # A 到 B 的距離，必須等於 B 到 A 的距離
    a_to_b = haversine(25.0339, 121.5645, 25.0478, 121.5170)
    b_to_a = haversine(25.0478, 121.5170, 25.0339, 121.5645)
    assert math.isclose(a_to_b, b_to_a)


def test_一公里約略正確():
    # 緯度每 0.009 度約等於 1 公里 (1 度緯度 ≈ 111 公里)
    # 從赤道往北 0.009 度，距離應該接近 1000 公尺，容許 ±50 公尺
    dist = haversine(0.0, 0.0, 0.009, 0.0)
    assert 950 < dist < 1050
