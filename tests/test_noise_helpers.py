"""
收斂用的雜訊小函式測試。

這些小函式把散落在 app.py 各處的 magic number（速度波動幅度、GPS 抖動標準差）
收斂成單一來源。測試驗證它們的輸出落在正確的統計範圍內。
"""
from domain.service.navigation import (
    speed_fluctuation_kmh,
    position_jitter_degrees,
)


def test_速度波動落在正負1點5之間():
    # 重複跑多次，結果都應落在 [-1.5, 1.5] km/h
    for _ in range(1000):
        v = speed_fluctuation_kmh()
        assert -1.5 <= v <= 1.5


def test_速度波動不是常數():
    # 確認它真的有隨機性（連跑兩次幾乎不可能相等）
    values = {speed_fluctuation_kmh() for _ in range(50)}
    assert len(values) > 1


def test_座標抖動大致以零為中心():
    # 高斯抖動 σ=0.000008，大量取樣平均值應接近 0（容許小誤差）
    n = 5000
    total = sum(position_jitter_degrees() for _ in range(n))
    avg = total / n
    assert abs(avg) < 0.000005


def test_座標抖動幅度合理():
    # σ=0.000008，單次抖動極少超過 6 個標準差（約 0.000048）
    for _ in range(1000):
        j = position_jitter_degrees()
        assert abs(j) < 0.000048
