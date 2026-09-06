"""
地理座標的純運算 —— 領域層 (domain) 的一部分。

這裡只放「純函式」：同樣輸入永遠得到同樣輸出，不碰 GUI、不碰 iPhone、
不碰檔案、不碰隨機。因此天生好測、天生可重用。

依賴規則 (見 .kiro/steering/ddd-clean-architecture.md)：
本檔只允許 import 標準庫 (math)，不得 import tkinter / pymobiledevice3 / requests。
"""
import math


def haversine(lat1, lon1, lat2, lon2):
    """Return distance in meters between two GPS points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
