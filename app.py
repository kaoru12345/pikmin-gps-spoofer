"""
Pikmin Bloom / Pokémon GO — USB GPS Spoofer with Auto-Navigation
Embedded map (tkintermapview) for click-to-set A/B points.
Uses iOS 17+ CoreDevice tunnel + DVT LocationSimulation API.
Requires: pymobiledevice3, requests, tkintermapview, iTunes (for usbmuxd)
Platform: Windows (Tkinter GUI)
"""

import subprocess
import sys

# ─── Auto-install missing dependencies ────────────────────────────────────────
def _ensure_packages():
    """Check and auto-install required packages on first run."""
    required = ["requests", "tkintermapview", "Pillow", "sv_ttk"]
    missing = []
    for pkg in required:
        import_name = "PIL" if pkg == "Pillow" else pkg
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[SETUP] 正在安裝缺少的套件: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[SETUP] 安裝完成！")

if not getattr(sys, 'frozen', False):
    # Only auto-install in dev/script mode, not in exe
    _ensure_packages()
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import json
import math
import os
import random
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, simpledialog, messagebox

# ─── Data persistence paths ──────────────────────────────────────────────────
# Dev mode: data/ next to app.py
# Exe mode: %USERPROFILE%/.pikmin-gps-spoofer/
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.join(os.path.expanduser("~"), ".pikmin-gps-spoofer")
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(_APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
LOCATIONS_FILE = os.path.join(DATA_DIR, "locations.json")
ROUTES_FILE = os.path.join(DATA_DIR, "routes.json")
STATE_FILE = os.path.join(DATA_DIR, "last_session.json")

try:
    import requests
except ImportError:
    requests = None

try:
    import tkintermapview
except ImportError:
    tkintermapview = None

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import sv_ttk
    HAS_SVTTK = True
except ImportError:
    HAS_SVTTK = False


# ─── Data Load/Save Helpers ───────────────────────────────────────────────────

def load_locations():
    if os.path.exists(LOCATIONS_FILE):
        with open(LOCATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_locations(locations):
    with open(LOCATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)

def load_routes():
    if os.path.exists(ROUTES_FILE):
        with open(ROUTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_routes(routes):
    with open(ROUTES_FILE, "w", encoding="utf-8") as f:
        json.dump(routes, f, ensure_ascii=False, indent=2)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─── Haversine & Geo Utils ────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    """Return distance in meters between two GPS points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def interpolate_points(coords, speed_mps, jitter_enabled):
    """
    Given a list of (lat, lon) waypoints and a base speed (m/s),
    yield interpolated (lat, lon) at ~1 Hz with speed fluctuation and optional jitter.
    """
    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        seg_dist = haversine(lat1, lon1, lat2, lon2)
        if seg_dist < 0.01:
            continue

        fluctuation = random.uniform(-1.5, 1.5) / 3.6
        actual_speed = max(0.5, speed_mps + fluctuation)

        steps = max(1, int(seg_dist / actual_speed))
        for s in range(steps):
            t = s / steps
            lat = lat1 + (lat2 - lat1) * t
            lon = lon1 + (lon2 - lon1) * t

            if jitter_enabled:
                lat += random.gauss(0, 0.000008)
                lon += random.gauss(0, 0.000008)

            fluctuation = random.uniform(-1.5, 1.5) / 3.6
            actual_speed = max(0.5, speed_mps + fluctuation)

            yield lat, lon, actual_speed * 3.6

    if coords:
        lat, lon = coords[-1]
        if jitter_enabled:
            lat += random.gauss(0, 0.000008)
            lon += random.gauss(0, 0.000008)
        yield lat, lon, 0.0


# ─── iPhone GPS Controller (iOS 17+ via CoreDevice Tunnel) ────────────────────

class iPhoneGPS:
    """
    Manages the tunnel and location simulation for iOS 17+.
    Uses a dedicated background thread with its own event loop to avoid
    'event loop already running' conflicts with Tkinter's main thread.
    """

    _instance = None  # Process-wide singleton (PyTCP only allows one tunnel)

    @classmethod
    def get_instance(cls):
        """Get or create the singleton iPhoneGPS instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._tunnel = None
        self._rsd = None
        self._dvt = None
        self._loc = None
        self._loop = None
        self._loop_thread = None
        self._device_info = ""
        self._connected = False

    @property
    def device_info(self):
        return self._device_info

    @property
    def connected(self):
        return self._connected

    def _start_loop_thread(self):
        """Start a dedicated event loop in a background thread."""
        if self._loop is not None and self._loop.is_running():
            return
        self._loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=_run_loop, daemon=True)
        self._loop_thread.start()

    def _run_async(self, coro):
        """Submit a coroutine to the dedicated loop and wait for result (thread-safe)."""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("Event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def connect(self):
        """Establish tunnel and open LocationSimulation channel. Returns device info string."""
        if self._connected:
            return self._device_info

        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.usbmux import list_devices
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        self._start_loop_thread()

        # Find device
        devices = self._run_async(list_devices())
        if not devices:
            raise ConnectionError("未偵測到 iPhone。請確認 USB 連接且已信任。")

        serial = devices[0].serial

        # Get device info
        lockdown = self._run_async(create_using_usbmux(serial=serial, autopair=True))
        self._device_info = f"{lockdown.product_type} iOS {lockdown.product_version}"

        # Open tunnel
        self._tunnel = UserspaceRsdTunnel(serial)
        self._rsd = self._run_async(self._tunnel.aopen())

        # Open DVT + LocationSimulation
        self._dvt = DvtProvider(self._rsd)
        self._loc = LocationSimulation(self._dvt)
        self._run_async(self._loc.__aenter__())

        self._connected = True
        return self._device_info

    def set_location(self, lat, lon):
        """Set the GPS coordinates on the iPhone."""
        if not self._connected or self._loc is None:
            raise RuntimeError("未連接裝置，請先呼叫 connect()")
        self._run_async(self._loc.set(lat, lon))

    def clear_location(self):
        """Restore real GPS."""
        if self._connected and self._loc is not None:
            try:
                self._run_async(self._loc.clear())
            except Exception:
                pass

    def disconnect(self):
        """Clean up tunnel and services."""
        if self._loc and self._loop:
            try:
                self._run_async(self._loc.__aexit__(None, None, None))
            except Exception:
                pass
        if self._tunnel and self._loop:
            try:
                self._run_async(self._tunnel.aclose())
            except Exception:
                pass
        # Stop the event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=5)
        self._loc = None
        self._dvt = None
        self._rsd = None
        self._tunnel = None
        self._loop = None
        self._loop_thread = None
        self._connected = False
        iPhoneGPS._instance = None  # Allow re-creation after full disconnect


# Need to import LocationSimulation at module level for type reference
try:
    from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
    HAS_PMD3 = True
except ImportError:
    HAS_PMD3 = False


# ─── Main Application ─────────────────────────────────────────────────────────

class GPSSpoofApp:
    def __init__(self, root):
        self.root = root
        # Get version from git tag
        import subprocess
        try:
            if getattr(sys, 'frozen', False):
                # Exe mode: extract version from exe filename (PikminGPS-v1.9.exe)
                exe_name = os.path.basename(sys.executable)
                import re
                m = re.search(r'v[\d.]+', exe_name)
                version = m.group(0) if m else "exe"
            else:
                # Dev mode: use git tag
                version = subprocess.check_output(
                    ["git", "describe", "--tags", "--always"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    stderr=subprocess.DEVNULL
                ).decode().strip()
        except Exception:
            version = "dev"
        self.root.title(f"Pikmin GPS Auto-Navigator {version}")
        self.root.geometry("1400x850")
        self.root.resizable(True, True)
        self.root.state("zoomed")

        # Windows title bar dark mode at startup
        if HAS_SVTTK:
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

        # Global font scaling — sv_ttk ignores named fonts, override via Style
        import tkinter.font as tkfont
        target_size = 13
        target_font = ("Segoe UI", target_size)

        # Named fonts (affects non-ttk widgets like ScrolledText, Listbox)
        for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(fname).configure(family="Segoe UI", size=target_size)
            except Exception:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=target_size)
        except Exception:
            pass

        # Override ttk widget fonts via Style (sv_ttk respects this)
        style = ttk.Style()
        style.configure(".", font=target_font)
        style.configure("TButton", font=target_font)
        style.configure("TLabel", font=target_font)
        style.configure("TCheckbutton", font=target_font)
        style.configure("TRadiobutton", font=target_font)
        style.configure("TEntry", font=target_font)
        style.configure("TCombobox", font=target_font)
        style.configure("TNotebook.Tab", font=target_font)
        style.configure("TLabelframe.Label", font=target_font)
        self.root.option_add("*TCombobox*Listbox.font", target_font)
        self.root.option_add("*Font", target_font)

        self._running = False
        self._drifting = False
        self._paused = False
        self._thread = None
        self._route_coords = []
        self._marker_a = None
        self._marker_b = None
        self._marker_c = None
        self._route_path = None
        self._current_marker = None
        self._click_mode = tk.StringVar(value="A")
        # Navigation progress (for mini mode)
        self._nav_seg_idx = 0
        self._nav_total_segs = 0
        self._nav_total_dist = 0
        self._nav_dist_done = 0
        self._nav_start_time = 0

        self._build_ui()
        self._restore_session()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _make_icon(self, name, size=18):
        """Generate a small colored icon by name. Returns PhotoImage or None."""
        if not HAS_PILLOW:
            return None
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = 2
        cx, cy = size // 2, size // 2
        r = size // 2 - pad

        icons_def = {
            # Navigation tab
            "map":      lambda: draw.polygon([(cx, pad), (size-pad, cy), (cx, size-pad), (pad, cy)],
                                             fill=(76, 175, 80)),
            "joystick": lambda: (draw.ellipse([pad, pad, size-pad, size-pad], fill=(66, 165, 245)),
                                 draw.ellipse([cx-3, cy-3, cx+3, cy+3], fill=(255, 255, 255, 230))),
            "wrench":   lambda: draw.rounded_rectangle([pad, pad, size-pad, size-pad], radius=4,
                                                       fill=(255, 167, 38)),
            "pencil":   lambda: draw.polygon([(pad+2, size-pad), (size-pad-2, pad+2),
                                              (size-pad, pad+4), (pad+4, size-pad)],
                                             fill=(171, 71, 188)),
            # Buttons
            "flower":   lambda: (draw.ellipse([pad+1, pad+1, size-pad-1, size-pad-1], fill=(255, 183, 197)),
                                 draw.ellipse([cx-3, cy-3, cx+3, cy+3], fill=(255, 235, 59))),
            "bolt":     lambda: draw.polygon([(cx+2, pad), (pad+2, cy+1), (cx-1, cy+1),
                                              (cx-2, size-pad), (size-pad-2, cy-1), (cx+1, cy-1)],
                                             fill=(255, 193, 7)),
            "pin":      lambda: (draw.ellipse([cx-4, pad+1, cx+4, cy+2], fill=(244, 67, 54)),
                                 draw.line([(cx, cy+2), (cx, size-pad)], fill=(244, 67, 54), width=2)),
            "route":    lambda: (draw.line([(pad+2, size-pad-2), (cx, pad+3), (size-pad-2, size-pad-2)],
                                           fill=(33, 150, 243), width=2),
                                 draw.ellipse([pad, size-pad-4, pad+4, size-pad], fill=(33, 150, 243))),
            "trash":    lambda: (draw.rectangle([pad+3, cy-2, size-pad-3, size-pad-2], fill=(158, 158, 158)),
                                 draw.rectangle([pad+2, cy-4, size-pad-2, cy-2], fill=(117, 117, 117)),
                                 draw.line([(cx, cy-5), (cx, cy-4)], fill=(117, 117, 117), width=2)),
            "spiral":   lambda: draw.arc([pad+2, pad+2, size-pad-2, size-pad-2], 0, 300,
                                         fill=(156, 39, 176), width=2),
            "pause":    lambda: (draw.rectangle([pad+3, pad+3, cx-2, size-pad-3], fill=(255, 152, 0)),
                                 draw.rectangle([cx+2, pad+3, size-pad-3, size-pad-3], fill=(255, 152, 0))),
            "stop":     lambda: draw.rectangle([pad+3, pad+3, size-pad-3, size-pad-3],
                                               fill=(244, 67, 54)),
            "down":     lambda: draw.polygon([(pad+2, pad+4), (size-pad-2, pad+4), (cx, size-pad-2)],
                                             fill=(96, 125, 139)),
            "search":   lambda: (draw.ellipse([pad+1, pad+1, size-pad-3, size-pad-3],
                                              outline=(33, 150, 243), width=2),
                                 draw.line([(size-pad-4, size-pad-4), (size-pad-1, size-pad-1)],
                                           fill=(33, 150, 243), width=2)),
            "paste":    lambda: (draw.rectangle([pad+2, pad+3, size-pad-2, size-pad-2],
                                               outline=(121, 85, 72), width=1),
                                 draw.rectangle([pad+4, pad+1, size-pad-4, pad+4], fill=(121, 85, 72))),
            "clipboard":lambda: (draw.rectangle([pad+1, pad+2, size-pad-1, size-pad-1],
                                               outline=(0, 150, 136), width=1),
                                 draw.rectangle([pad+4, pad+5, size-pad-4, size-pad-4],
                                               fill=(0, 150, 136, 120))),
            "swap":     lambda: (draw.line([(cx, pad+2), (cx, size-pad-2)], fill=(0, 150, 136), width=2),
                                 draw.polygon([(cx-3, pad+5), (cx+3, pad+5), (cx, pad+1)],
                                              fill=(0, 150, 136)),
                                 draw.polygon([(cx-3, size-pad-5), (cx+3, size-pad-5), (cx, size-pad-1)],
                                              fill=(0, 150, 136))),
            "export":   lambda: (draw.polygon([(cx, pad+2), (cx-4, cy), (cx+4, cy)], fill=(76, 175, 80)),
                                 draw.rectangle([cx-2, cy, cx+2, size-pad-3], fill=(76, 175, 80)),
                                 draw.line([(pad+2, size-pad-2), (size-pad-2, size-pad-2)],
                                           fill=(76, 175, 80), width=2)),
            "import":   lambda: (draw.polygon([(cx, size-pad-3), (cx-4, cy), (cx+4, cy)],
                                              fill=(33, 150, 243)),
                                 draw.rectangle([cx-2, pad+3, cx+2, cy], fill=(33, 150, 243)),
                                 draw.line([(pad+2, size-pad-2), (size-pad-2, size-pad-2)],
                                           fill=(33, 150, 243), width=2)),
            "devmode":  lambda: (draw.rounded_rectangle([pad+1, pad+1, size-pad-1, size-pad-1],
                                                        radius=3, outline=(255, 167, 38), width=2),
                                 draw.text((pad+4, pad+2), ">_", fill=(255, 167, 38))),
            "rainbow":  lambda: (draw.arc([pad, pad+2, size-pad, size-pad+4], 0, 180,
                                          fill=(244, 67, 54), width=2),
                                 draw.arc([pad+2, pad+4, size-pad-2, size-pad+2], 0, 180,
                                          fill=(255, 193, 7), width=2),
                                 draw.arc([pad+4, pad+6, size-pad-4, size-pad], 0, 180,
                                          fill=(76, 175, 80), width=2)),
            "phone":    lambda: draw.rounded_rectangle([cx-4, pad+1, cx+4, size-pad-1], radius=2,
                                                       fill=(66, 165, 245)),
            "draw_pin": lambda: (draw.ellipse([cx-4, pad+1, cx+4, cy+2], fill=(171, 71, 188)),
                                 draw.line([(cx, cy+2), (cx, size-pad)], fill=(171, 71, 188), width=2)),
            "undo":     lambda: draw.arc([pad+2, pad+3, size-pad-2, size-pad-3], 90, 340,
                                         fill=(33, 150, 243), width=2),
            "check":    lambda: draw.line([(pad+3, cy), (cx-1, size-pad-3), (size-pad-3, pad+4)],
                                          fill=(76, 175, 80), width=3),
            "fly":      lambda: draw.polygon([(cx, pad+1), (pad+2, size-pad-2), (size-pad-2, size-pad-2)],
                                             fill=(33, 150, 243)),
            "plus":     lambda: (draw.line([(cx, pad+3), (cx, size-pad-3)], fill=(76, 175, 80), width=3),
                                 draw.line([(pad+3, cy), (size-pad-3, cy)], fill=(76, 175, 80), width=3)),
            "cross":    lambda: (draw.line([(pad+3, pad+3), (size-pad-3, size-pad-3)],
                                           fill=(244, 67, 54), width=2),
                                 draw.line([(size-pad-3, pad+3), (pad+3, size-pad-3)],
                                           fill=(244, 67, 54), width=2)),
            "edit":     lambda: (draw.polygon([(pad+2, size-pad-1), (size-pad-4, pad+3),
                                              (size-pad-1, pad+6), (pad+5, size-pad-1)],
                                             fill=(255, 167, 38)),
                                 draw.polygon([(pad, size-pad), (pad+2, size-pad-1),
                                              (pad+5, size-pad-1), (pad, size-pad)],
                                             fill=(80, 80, 80))),
            "load":     lambda: (draw.rectangle([pad+3, pad+2, size-pad-3, size-pad-2],
                                               outline=(33, 150, 243), width=1),
                                 draw.polygon([(cx, cy+3), (cx-4, cy-1), (cx+4, cy-1)],
                                              fill=(33, 150, 243))),
            "set":      lambda: draw.rounded_rectangle([pad+2, pad+2, size-pad-2, size-pad-2],
                                                       radius=3, fill=(0, 150, 136)),
        }

        if name in icons_def:
            icons_def[name]()

        photo = ImageTk.PhotoImage(img)
        if not hasattr(self, '_tab_icons'):
            self._tab_icons = []
        self._tab_icons.append(photo)  # prevent GC
        return photo

    def _build_ui(self):
        # ── Generate all icons upfront ──
        self._icon_nav = self._make_icon("map", size=20)
        self._icon_joy = self._make_icon("joystick", size=20)
        self._icon_tool = self._make_icon("wrench", size=20)
        self._icon_draw = self._make_icon("pencil", size=20)
        self._icon_flower = self._make_icon("flower")
        self._icon_bolt = self._make_icon("bolt")
        self._icon_pin = self._make_icon("pin")
        self._icon_route = self._make_icon("route")
        self._icon_trash = self._make_icon("trash")
        self._icon_spiral = self._make_icon("spiral")
        self._icon_pause = self._make_icon("pause")
        self._icon_stop = self._make_icon("stop")
        self._icon_down = self._make_icon("down")
        self._icon_search = self._make_icon("search")
        self._icon_clipboard = self._make_icon("clipboard")
        self._icon_swap = self._make_icon("swap")
        self._icon_export = self._make_icon("export")
        self._icon_import = self._make_icon("import")
        self._icon_devmode = self._make_icon("devmode")
        self._icon_rainbow = self._make_icon("rainbow")
        self._icon_phone = self._make_icon("phone")
        self._icon_draw_pin = self._make_icon("draw_pin")
        self._icon_undo = self._make_icon("undo")
        self._icon_check = self._make_icon("check")
        self._icon_fly = self._make_icon("fly")
        self._icon_plus = self._make_icon("plus")
        self._icon_cross = self._make_icon("cross")
        self._icon_edit = self._make_icon("edit")
        self._icon_load = self._make_icon("load")
        self._icon_set = self._make_icon("set")

        # ── Main paned layout: left=(map+log), right=controls ──
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Left: Map + Log (vertical split) ──
        frame_left = ttk.Frame(self.root)
        paned.add(frame_left, weight=10)

        frame_map = ttk.LabelFrame(frame_left, text="地圖 (左鍵點擊設定 A/B 點，右鍵選單)", padding=5)
        frame_map.pack(fill="both", expand=True)

        if tkintermapview:
            tile_cache_path = os.path.join(DATA_DIR, "map_tiles.db")
            self.map_widget = tkintermapview.TkinterMapView(frame_map, width=600, height=500,
                                                            database_path=tile_cache_path)
            self.map_widget.pack(fill="both", expand=True)
            self.map_widget.set_position(35.6812, 139.7671)
            self.map_widget.set_zoom(14)
            self.map_widget.set_tile_server(
                "https://mt0.google.com/vt/lyrs=m&hl=zh-TW&x={x}&y={y}&z={z}&s=Ga", max_zoom=20
            )
            self.map_widget.add_right_click_menu_command(
                label="設為起點 A", command=self._set_point_a_from_menu, pass_coords=True
            )
            self.map_widget.add_right_click_menu_command(
                label="設為經過 B", command=self._set_point_b_from_menu, pass_coords=True
            )
            self.map_widget.add_right_click_menu_command(
                label="設為終點 C", command=self._set_point_c_from_menu, pass_coords=True
            )
            self.map_widget.add_left_click_map_command(self._on_map_click)
        else:
            ttk.Label(frame_map, text="⚠ pip install tkintermapview", font=("Arial", 12)).pack(expand=True)
            self.map_widget = None

        # ── Log + Saved (under map, side by side) ──
        frame_bottom = ttk.Frame(frame_left)
        frame_bottom.pack(fill="both", expand=False, pady=(5, 0))

        frame_log = ttk.LabelFrame(frame_bottom, text="狀態日誌", padding=5)
        frame_log.pack(side="left", fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(frame_log, height=8, state="disabled", font=("Consolas", 13))
        self.log.pack(fill="both", expand=True)
        # Match log colors to initial theme
        if HAS_SVTTK:
            self.log.configure(bg="#1c1c1c", fg="#d4d4d4", insertbackground="#d4d4d4")

        frame_saved = ttk.Frame(frame_bottom)
        frame_saved.pack(side="left", fill="both", padx=(5, 0))

        # ── Saved Locations ──
        frame_loc = ttk.LabelFrame(frame_saved, text="收藏地點", padding=5)
        frame_loc.pack(fill="x", pady=(0, 5))

        # Category filter
        frame_loc_cat = ttk.Frame(frame_loc)
        frame_loc_cat.pack(fill="x", pady=(0, 3))
        self._loc_category = tk.StringVar(value="全部")
        ttk.Label(frame_loc_cat, text="分類:").pack(side="left")
        self._loc_cat_combo = ttk.Combobox(frame_loc_cat, textvariable=self._loc_category,
                                            state="readonly", width=8,
                                            values=["全部", "純點", "菇點", "明信片點", "我的最愛", "活動點"])
        self._loc_cat_combo.pack(side="left", padx=2)
        self._loc_cat_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_saved_locations())

        self._loc_var = tk.StringVar()
        self._loc_combo = ttk.Combobox(frame_loc, textvariable=self._loc_var, state="readonly", width=18)
        self._loc_combo.pack(fill="x", pady=(0, 3))

        frame_loc_btns = ttk.Frame(frame_loc)
        frame_loc_btns.pack(fill="x")
        ttk.Button(frame_loc_btns, text=" 飛", width=5, image=self._icon_fly, compound="left", command=self._teleport_to_saved_loc).pack(side="left", padx=2)
        ttk.Button(frame_loc_btns, text=" +", width=5, image=self._icon_plus, compound="left", command=self._save_current_location).pack(side="left", padx=2)
        ttk.Button(frame_loc_btns, text=" ✕", width=5, image=self._icon_cross, compound="left", command=self._delete_saved_location).pack(side="left", padx=2)
        ttk.Button(frame_loc_btns, text=" 改", width=5, image=self._icon_edit, compound="left", command=self._change_location_category).pack(side="left", padx=2)

        # ── Saved Routes ──
        frame_routes = ttk.LabelFrame(frame_saved, text="收藏路徑", padding=5)
        frame_routes.pack(fill="x", pady=(0, 5))

        self._route_var = tk.StringVar()
        self._route_combo = ttk.Combobox(frame_routes, textvariable=self._route_var, state="readonly", width=18)
        self._route_combo.pack(fill="x", pady=(0, 3))

        frame_route_btns2 = ttk.Frame(frame_routes)
        frame_route_btns2.pack(fill="x")
        ttk.Button(frame_route_btns2, text=" 載入", width=5, image=self._icon_load, compound="left", command=self._load_saved_route).pack(side="left", padx=2)
        ttk.Button(frame_route_btns2, text=" +", width=5, image=self._icon_plus, compound="left", command=self._save_current_route).pack(side="left", padx=2)
        ttk.Button(frame_route_btns2, text=" ✕", width=5, image=self._icon_cross, compound="left", command=self._delete_saved_route).pack(side="left", padx=2)

        # ── Export / Import ──
        frame_io = ttk.Frame(frame_saved)
        frame_io.pack(fill="x", pady=(5, 0))
        ttk.Button(frame_io, text=" 匯出資料", width=10, image=self._icon_export, compound="left", command=self._export_data).pack(side="left", padx=2)
        ttk.Button(frame_io, text=" 匯入資料", width=10, image=self._icon_import, compound="left", command=self._import_data).pack(side="left", padx=2)

        # ── Right: Controls (Tabbed) ──
        frame_right = ttk.Frame(self.root)
        paned.add(frame_right, weight=1)

        # ── Click mode + Dark mode (always visible above tabs) ──
        frame_mode = ttk.LabelFrame(frame_right, text="點擊模式", padding=5)
        frame_mode.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Radiobutton(frame_mode, text="設定起點 A", variable=self._click_mode, value="A").pack(side="left", padx=10)
        ttk.Radiobutton(frame_mode, text="設定經過 B", variable=self._click_mode, value="B").pack(side="left", padx=10)
        ttk.Radiobutton(frame_mode, text="設定終點 C", variable=self._click_mode, value="C").pack(side="left", padx=10)

        self._dark_mode = tk.BooleanVar(value=HAS_SVTTK)  # Default dark if sv_ttk available
        ttk.Checkbutton(frame_mode, text="🌙", variable=self._dark_mode,
                        command=self._toggle_dark_mode).pack(side="right", padx=5)

        # ── Notebook (Tabs) ──
        self._notebook = ttk.Notebook(frame_right)
        self._notebook.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # ════════════════════════════════════════════════════════════════════
        # TAB 1: 導航
        # ════════════════════════════════════════════════════════════════════
        tab_nav = ttk.Frame(self._notebook)
        if self._icon_nav:
            self._notebook.add(tab_nav, text=" 導航", image=self._icon_nav, compound="left")
        else:
            self._notebook.add(tab_nav, text="🗺 導航")

        # ── Search ──
        frame_search = ttk.LabelFrame(tab_nav, text="搜尋地點", padding=5)
        frame_search.pack(fill="x", padx=5, pady=(5, 5))

        self.entry_search = ttk.Entry(frame_search, width=15)
        self.entry_search.pack(side="left", padx=(0, 5))
        self.entry_search.bind("<Return>", lambda e: self._search_location())

        ttk.Button(frame_search, text=" 搜尋", width=6, image=self._icon_search, compound="left", command=self._search_location).pack(side="left")

        # ── Paste Coordinates ──
        frame_paste = ttk.LabelFrame(tab_nav, text="貼上座標 (格式: lat, lon)", padding=5)
        frame_paste.pack(fill="x", padx=5, pady=(0, 5))

        self.entry_paste = ttk.Entry(frame_paste, width=15)
        self.entry_paste.pack(side="left", padx=(0, 5))
        self.entry_paste.bind("<Return>", lambda e: self._paste_coords())

        ttk.Button(frame_paste, text=" 設定", width=6, image=self._icon_set, compound="left", command=self._paste_coords).pack(side="left")
        ttk.Button(frame_paste, text="", width=3, image=self._icon_clipboard, compound="left", command=self._paste_from_clipboard).pack(side="left", padx=2)

        # ── Input Frame ──
        frame_input = ttk.LabelFrame(tab_nav, text="路徑設定", padding=10)
        frame_input.pack(fill="x", padx=5, pady=(5, 5))

        ttk.Label(frame_input, text="起點 A (lat, lng):").grid(row=0, column=0, sticky="w")
        self.entry_a_lat = ttk.Entry(frame_input, width=10)
        self.entry_a_lat.grid(row=0, column=1, padx=2)
        self.entry_a_lat.insert(0, "35.6812")
        self.entry_a_lng = ttk.Entry(frame_input, width=10)
        self.entry_a_lng.grid(row=0, column=2, padx=2)
        self.entry_a_lng.insert(0, "139.7671")

        ttk.Label(frame_input, text="經過 B (lat, lng):").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.entry_b_lat = ttk.Entry(frame_input, width=10)
        self.entry_b_lat.grid(row=1, column=1, padx=2, pady=(5, 0))
        self.entry_b_lat.insert(0, "35.6895")
        self.entry_b_lng = ttk.Entry(frame_input, width=10)
        self.entry_b_lng.grid(row=1, column=2, padx=2, pady=(5, 0))
        self.entry_b_lng.insert(0, "139.6917")

        ttk.Label(frame_input, text="終點 C (lat, lng):").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.entry_c_lat = ttk.Entry(frame_input, width=10)
        self.entry_c_lat.grid(row=2, column=1, padx=2, pady=(5, 0))
        self.entry_c_lng = ttk.Entry(frame_input, width=10)
        self.entry_c_lng.grid(row=2, column=2, padx=2, pady=(5, 0))

        ttk.Button(frame_input, text=" A↔C", width=8, image=self._icon_swap, compound="left", command=self._swap_ac).grid(row=0, column=3, rowspan=3, padx=5, sticky="ns")

        ttk.Label(frame_input, text="時速 (km/h):").grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.speed_var = tk.DoubleVar(value=10.0)
        self.entry_speed = ttk.Entry(frame_input, width=6, textvariable=self.speed_var)
        self.entry_speed.grid(row=3, column=1, sticky="w", padx=2, pady=(5, 0))

        self.speed_scale = ttk.Scale(frame_input, from_=1, to=20, variable=self.speed_var,
                                      orient="horizontal", length=120,
                                      command=lambda v: self.speed_var.set(round(float(v), 1)))
        self.speed_scale.grid(row=4, column=0, columnspan=3, sticky="we", pady=(5, 0))

        self.var_jitter = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame_input, text="GPS Jitter", variable=self.var_jitter).grid(
            row=3, column=2, sticky="w", pady=(5, 0)
        )

        ttk.Label(frame_input, text="路線模式:").grid(row=5, column=0, sticky="w", pady=(5, 0))
        self.route_mode_var = tk.StringVar(value="foot")
        frame_route_mode = ttk.Frame(frame_input)
        frame_route_mode.grid(row=5, column=1, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Radiobutton(frame_route_mode, text="步行", variable=self.route_mode_var, value="foot").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(frame_route_mode, text="腳踏車", variable=self.route_mode_var, value="bike").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(frame_route_mode, text="開車", variable=self.route_mode_var, value="driving").pack(side="left")

        # ── Navigation Buttons ──
        frame_btn = ttk.Frame(tab_nav, padding=5)
        frame_btn.pack(fill="x", padx=5)

        frame_tp = ttk.Frame(frame_btn)
        frame_tp.pack(fill="x", pady=2)
        frame_tp.columnconfigure(0, weight=1, uniform="btn")
        frame_tp.columnconfigure(1, weight=1, uniform="btn")
        self.btn_teleport = ttk.Button(frame_tp, text=" 瞬移到 A 點", image=self._icon_bolt, compound="left", command=self._teleport_to_a)
        self.btn_teleport.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_release = ttk.Button(frame_tp, text=" 恢復真實 GPS", image=self._icon_pin, compound="left", command=self._release_gps)
        self.btn_release.grid(row=0, column=1, sticky="we", padx=(2, 0))

        frame_fetch_btns = ttk.Frame(frame_btn)
        frame_fetch_btns.pack(fill="x", pady=2)
        frame_fetch_btns.columnconfigure(0, weight=1, uniform="btn")
        frame_fetch_btns.columnconfigure(1, weight=1, uniform="btn")
        self.btn_fetch = ttk.Button(frame_fetch_btns, text=" 抓取道路路徑", image=self._icon_route, compound="left", command=self._fetch_route)
        self.btn_fetch.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_clear_route = ttk.Button(frame_fetch_btns, text=" 清除路徑", image=self._icon_trash, compound="left", command=self._clear_route)
        self.btn_clear_route.grid(row=0, column=1, sticky="we", padx=(2, 0))

        # Spiral + stop + pause (smaller row)
        frame_spiral_stop = ttk.Frame(frame_btn)
        frame_spiral_stop.pack(fill="x", pady=2)
        frame_spiral_stop.columnconfigure(0, weight=1, uniform="btn")
        frame_spiral_stop.columnconfigure(1, weight=1, uniform="btn")
        frame_spiral_stop.columnconfigure(2, weight=1, uniform="btn")
        self.btn_spiral = ttk.Button(frame_spiral_stop, text=" 繞圈種花", image=self._icon_spiral, compound="left", command=self._start_spiral)
        self.btn_spiral.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_pause = ttk.Button(frame_spiral_stop, text=" 暫停", image=self._icon_pause, compound="left", command=self._toggle_pause)
        self.btn_pause.grid(row=0, column=1, sticky="we", padx=2)
        self.btn_stop = ttk.Button(frame_spiral_stop, text=" 停止", image=self._icon_stop, compound="left", command=self._stop_navigation)
        self.btn_stop.grid(row=0, column=2, sticky="we", padx=(2, 0))

        # Main action button — big and prominent
        self.btn_start = ttk.Button(frame_btn, text=" 開始自動種花", image=self._icon_flower, compound="left", command=self._start_navigation)
        self.btn_start.pack(fill="x", pady=(5, 2), ipady=8)

        # Navigation progress / remaining time display
        self._nav_status_label = ttk.Label(frame_btn, text="", font=("Consolas", 11),
                                           anchor="center")
        self._nav_status_label.pack(fill="x", pady=(2, 2))

        # Mini mode button
        ttk.Button(frame_btn, text=" 迷你模式", image=self._icon_down, compound="left", command=self._enter_mini_mode).pack(fill="x", pady=2)

        # ════════════════════════════════════════════════════════════════════
        # TAB 2: 方向控制 (Joystick)
        # ════════════════════════════════════════════════════════════════════
        tab_joystick = ttk.Frame(self._notebook)
        if self._icon_joy:
            self._notebook.add(tab_joystick, text=" 方向控制", image=self._icon_joy, compound="left")
        else:
            self._notebook.add(tab_joystick, text="🕹 方向控制")

        self._manual_lat = None
        self._manual_lon = None
        self._joystick_step = tk.DoubleVar(value=5.0)

        # Step size
        frame_jstep = ttk.LabelFrame(tab_joystick, text="步距設定", padding=5)
        frame_jstep.pack(fill="x", padx=5, pady=(5, 5))
        ttk.Label(frame_jstep, text="每步距離 (公尺):").pack(side="left")
        ttk.Entry(frame_jstep, textvariable=self._joystick_step, width=6).pack(side="left", padx=5)
        ttk.Scale(frame_jstep, from_=1, to=50, variable=self._joystick_step,
                  orient="horizontal", length=120).pack(side="left", fill="x", expand=True)

        # Quick presets
        frame_jpresets = ttk.Frame(tab_joystick)
        frame_jpresets.pack(fill="x", padx=5, pady=(0, 5))
        for val, label in [(2, "2m"), (5, "5m"), (10, "10m"), (25, "25m"), (50, "50m")]:
            ttk.Button(frame_jpresets, text=label, width=5,
                       command=lambda v=val: self._joystick_step.set(v)).pack(side="left", padx=3)

        # 3x3 D-pad
        frame_dpad = ttk.LabelFrame(tab_joystick, text="方向鍵 (或鍵盤 WASD / 方向鍵)", padding=10)
        frame_dpad.pack(padx=5, pady=5)

        for col in range(3):
            frame_dpad.columnconfigure(col, weight=1, uniform="dpad")
        for row in range(3):
            frame_dpad.rowconfigure(row, weight=1, uniform="dpad")

        dpad_buttons = [
            ("左上", -1, 1, 0, 0),
            ("上",    0, 1, 0, 1),
            ("右上",  1, 1, 0, 2),
            ("左",   -1, 0, 1, 0),
            ("回A",   0, 0, 1, 1),
            ("右",    1, 0, 1, 2),
            ("左下", -1,-1, 2, 0),
            ("下",    0,-1, 2, 1),
            ("右下",  1,-1, 2, 2),
        ]
        for text, dx, dy, row, col in dpad_buttons:
            if text == "回A":
                btn = ttk.Button(frame_dpad, text=text, width=5, command=self._joystick_center)
            else:
                btn = ttk.Button(frame_dpad, text=text, width=5,
                                 command=lambda x=dx, y=dy: self._joystick_move(x, y))
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        # Position label
        self._joystick_pos_label = ttk.Label(tab_joystick, text="目前位置: (按方向鍵開始)",
                                             font=("Consolas", 11))
        self._joystick_pos_label.pack(fill="x", padx=5, pady=(10, 5))

        # Set A point to current joystick position
        ttk.Button(tab_joystick, text=" 設定 A 點為目前位置", image=self._icon_draw_pin, compound="left",
                   command=self._joystick_set_a_to_current).pack(fill="x", padx=5, pady=(0, 5))

        # Hint
        ttk.Label(tab_joystick, text="快捷鍵: W/A/S/D=上下左右, Q/E/Z/C=斜向",
                  font=("Segoe UI", 9), foreground="gray").pack(fill="x", padx=5)

        # Keyboard bindings for WASD / arrow keys
        self.root.bind("<Key>", self._joystick_key_handler)

        # ════════════════════════════════════════════════════════════════════
        # TAB 3: 工具
        # ════════════════════════════════════════════════════════════════════
        tab_tools = ttk.Frame(self._notebook)
        if self._icon_tool:
            self._notebook.add(tab_tools, text=" 工具", image=self._icon_tool, compound="left")
        else:
            self._notebook.add(tab_tools, text="🔧 工具")

        # ── Other Tools ──
        frame_tools_btn = ttk.Frame(tab_tools, padding=5)
        frame_tools_btn.pack(fill="x", padx=5)

        self.btn_devmode = ttk.Button(frame_tools_btn, text=" 一鍵開啟開發者模式", image=self._icon_devmode, compound="left", command=self._enable_dev_mode)
        self.btn_devmode.pack(fill="x", pady=2)

        frame_flash_mirror = ttk.Frame(frame_tools_btn)
        frame_flash_mirror.pack(fill="x", pady=2)
        frame_flash_mirror.columnconfigure(0, weight=1, uniform="btn")
        frame_flash_mirror.columnconfigure(1, weight=1, uniform="btn")
        self.btn_flash = ttk.Button(frame_flash_mirror, text=" 閃爍模式", image=self._icon_rainbow, compound="left", command=self._flash_mode)
        self.btn_flash.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_mirror = ttk.Button(frame_flash_mirror, text=" 手機投影", image=self._icon_phone, compound="left", command=self._toggle_screen_mirror)
        self.btn_mirror.grid(row=0, column=1, sticky="we", padx=(2, 0))

        self._refresh_saved_locations()
        self._refresh_saved_routes()

        # ════════════════════════════════════════════════════════════════════
        # TAB 4: 手繪路徑
        # ════════════════════════════════════════════════════════════════════
        tab_draw = ttk.Frame(self._notebook)
        if self._icon_draw:
            self._notebook.add(tab_draw, text=" 手繪路徑", image=self._icon_draw, compound="left")
        else:
            self._notebook.add(tab_draw, text="✏ 手繪路徑")

        self._draw_mode = False
        self._draw_drag_mode = False  # True = drag to draw, False = click to draw
        self._draw_points = []  # list of (lat, lon)
        self._draw_markers = []
        self._draw_path = None
        self._draw_last_drag_pos = None  # for throttling drag events

        ttk.Label(tab_draw, text="在地圖上畫出你想走的路線\n畫完後按「生成路徑」即可使用",
                  font=("Segoe UI", 10), justify="left").pack(fill="x", padx=5, pady=(5, 5))

        # Draw method selection
        frame_draw_method = ttk.LabelFrame(tab_draw, text="繪製方式", padding=5)
        frame_draw_method.pack(fill="x", padx=5, pady=(0, 5))

        self._draw_method = tk.StringVar(value="click")
        ttk.Radiobutton(frame_draw_method, text="點擊加點", variable=self._draw_method,
                        value="click").pack(side="left", padx=10)
        ttk.Radiobutton(frame_draw_method, text="拖曳畫線", variable=self._draw_method,
                        value="drag").pack(side="left", padx=10)

        # Drag sampling distance
        frame_drag_cfg = ttk.Frame(tab_draw)
        frame_drag_cfg.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Label(frame_drag_cfg, text="拖曳取樣間距:").pack(side="left")
        self._draw_drag_interval = tk.IntVar(value=30)
        ttk.Entry(frame_drag_cfg, textvariable=self._draw_drag_interval, width=5).pack(side="left", padx=3)
        ttk.Label(frame_drag_cfg, text="公尺 (越小越精細)").pack(side="left")

        # Control buttons
        frame_draw_btns = ttk.Frame(tab_draw, padding=5)
        frame_draw_btns.pack(fill="x", padx=5)

        self.btn_draw_start = ttk.Button(frame_draw_btns, text=" 開始畫路徑", image=self._icon_draw_pin, compound="left",
                                          command=self._draw_start)
        self.btn_draw_start.pack(fill="x", pady=2)

        self.btn_draw_undo = ttk.Button(frame_draw_btns, text=" 撤回上一點", image=self._icon_undo, compound="left",
                                         command=self._draw_undo)
        self.btn_draw_undo.pack(fill="x", pady=2)

        self.btn_draw_clear = ttk.Button(frame_draw_btns, text=" 清除所有點", image=self._icon_trash, compound="left",
                                          command=self._draw_clear)
        self.btn_draw_clear.pack(fill="x", pady=2)

        # Snap to road option
        self._draw_snap_road = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame_draw_btns, text="自動對齊道路 (用路徑 API)",
                        variable=self._draw_snap_road).pack(fill="x", pady=2)

        self.btn_draw_generate = ttk.Button(frame_draw_btns, text=" 生成路徑", image=self._icon_check, compound="left",
                                             command=self._draw_generate)
        self.btn_draw_generate.pack(fill="x", pady=(5, 2))

        ttk.Button(frame_draw_btns, text=" 清除已生成路徑", image=self._icon_trash, compound="left",
                   command=self._clear_route).pack(fill="x", pady=2)

        # Info label
        self._draw_info_label = ttk.Label(tab_draw, text="路徑點: 0", font=("Consolas", 10))
        self._draw_info_label.pack(fill="x", padx=5, pady=(5, 5))

        # Point list
        frame_draw_list = ttk.LabelFrame(tab_draw, text="路徑點列表", padding=5)
        frame_draw_list.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self._draw_listbox = tk.Listbox(frame_draw_list, height=8, font=("Consolas", 9))
        self._draw_listbox.pack(fill="both", expand=True)

    # ── Joystick / Direction Pad ──

    def _joystick_key_handler(self, event):
        """Handle keyboard shortcuts for joystick (WASD + QE/ZC or arrow keys).
        Only fire if focus is NOT on an Entry/Combobox/Text widget."""
        focused = self.root.focus_get()
        if focused and (isinstance(focused, (ttk.Entry, tk.Entry, tk.Text, scrolledtext.ScrolledText))):
            return  # Don't hijack typing in input fields

        key = event.keysym.lower()
        mapping = {
            'w': (0, 1), 'up': (0, 1),
            's': (0, -1), 'down': (0, -1),
            'a': (-1, 0), 'left': (-1, 0),
            'd': (1, 0), 'right': (1, 0),
            'q': (-1, 1),   # ↖
            'e': (1, 1),    # ↗
            'z': (-1, -1),  # ↙
            'c': (1, -1),   # ↘
        }
        if key in mapping:
            dx, dy = mapping[key]
            self._joystick_move(dx, dy)

    def _joystick_move(self, dx, dy):
        """Move manual GPS position by (dx, dy) * step_meters.
        dx: -1=west, +1=east. dy: -1=south, +1=north."""
        # Stop any active drift/nav first so it doesn't override our injection
        self._drifting = False
        self._running = False

        # Initialize manual position from A point if not set
        if self._manual_lat is None or self._manual_lon is None:
            try:
                self._manual_lat = float(self.entry_a_lat.get())
                self._manual_lon = float(self.entry_a_lng.get())
            except ValueError:
                self._log("[JOYSTICK] 請先設定 A 點座標。")
                return

        try:
            step_m = float(self._joystick_step.get())
        except (ValueError, tk.TclError):
            step_m = 5.0

        # Convert meters to lat/lon offset
        # 1 degree lat ≈ 111,320 m
        # 1 degree lon ≈ 111,320 * cos(lat) m
        dlat = (dy * step_m) / 111320.0
        dlon = (dx * step_m) / (111320.0 * math.cos(math.radians(self._manual_lat)))

        # Diagonal: normalize so total distance = step_m (not step_m * sqrt(2))
        if dx != 0 and dy != 0:
            factor = 1.0 / math.sqrt(2)
            dlat *= factor
            dlon *= factor

        self._manual_lat += dlat
        self._manual_lon += dlon

        # Update label
        self._joystick_pos_label.config(text=f"({self._manual_lat:.6f}, {self._manual_lon:.6f})")

        # Update map marker
        if self.map_widget:
            self._update_current_marker(self._manual_lat, self._manual_lon)

        # Log movement
        self._log(f"[JOYSTICK] ({self._manual_lat:.6f}, {self._manual_lon:.6f}) 步距={step_m:.0f}m")

        # Inject GPS location (with small delay to let drift thread exit)
        lat_to_set = self._manual_lat
        lon_to_set = self._manual_lon

        def _do():
            time.sleep(0.15)  # Let drift loop exit
            if not HAS_PMD3:
                return
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")
                gps.set_location(lat_to_set, lon_to_set)
            except Exception as e:
                self._log(f"[JOYSTICK] GPS 注入失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _joystick_center(self):
        """Reset manual position back to A point and inject."""
        # Stop drift/nav
        self._drifting = False
        self._running = False

        try:
            self._manual_lat = float(self.entry_a_lat.get())
            self._manual_lon = float(self.entry_a_lng.get())
        except ValueError:
            self._log("[JOYSTICK] A 點座標無效。")
            return

        self._joystick_pos_label.config(text=f"({self._manual_lat:.6f}, {self._manual_lon:.6f})")

        if self.map_widget:
            self._update_current_marker(self._manual_lat, self._manual_lon)
            self.map_widget.set_position(self._manual_lat, self._manual_lon)

        self._log(f"[JOYSTICK] 回到 A 點 ({self._manual_lat:.6f}, {self._manual_lon:.6f})")

        lat_to_set = self._manual_lat
        lon_to_set = self._manual_lon

        def _do():
            time.sleep(0.15)
            if not HAS_PMD3:
                return
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")
                gps.set_location(lat_to_set, lon_to_set)
            except Exception as e:
                self._log(f"[JOYSTICK] GPS 注入失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _joystick_set_a_to_current(self):
        """Set A point to the current joystick position."""
        if self._manual_lat is None or self._manual_lon is None:
            self._log("[JOYSTICK] 尚未移動，沒有目前位置。")
            return
        self._set_point_a(self._manual_lat, self._manual_lon)
        self._log(f"[JOYSTICK] A 點已設為目前位置 ({self._manual_lat:.6f}, {self._manual_lon:.6f})")

    # ── Mini Mode ──

    def _enter_mini_mode(self):
        """Hide main window and show a small floating status window."""
        # Create mini window
        self._mini_win = tk.Toplevel(self.root)
        self._mini_win.title("Pikmin GPS")
        self._mini_win.attributes("-topmost", True)
        self._mini_win.resizable(False, False)
        self._mini_win.configure(bg="#1e1e1e")
        self._mini_win.protocol("WM_DELETE_WINDOW", self._exit_mini_mode)

        # Size and position: small, bottom-right of screen
        win_w, win_h = 280, 120
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - win_w - 20
        y = screen_h - win_h - 60
        self._mini_win.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # Make it draggable
        self._mini_win.bind("<Button-1>", self._mini_drag_start)
        self._mini_win.bind("<B1-Motion>", self._mini_drag_motion)

        # Status label
        self._mini_status = tk.Label(self._mini_win, text="🌸 種花中...",
                                     bg="#1e1e1e", fg="#88cc88", font=("Segoe UI", 11, "bold"))
        self._mini_status.pack(fill="x", padx=8, pady=(8, 2))
        self._mini_status.bind("<Button-1>", self._mini_drag_start)
        self._mini_status.bind("<B1-Motion>", self._mini_drag_motion)

        # Info label (speed + coords)
        self._mini_info = tk.Label(self._mini_win, text="",
                                   bg="#1e1e1e", fg="#aaaaaa", font=("Consolas", 9))
        self._mini_info.pack(fill="x", padx=8)
        self._mini_info.bind("<Button-1>", self._mini_drag_start)
        self._mini_info.bind("<B1-Motion>", self._mini_drag_motion)

        # Buttons row (centered)
        frame_mini_btns = tk.Frame(self._mini_win, bg="#1e1e1e")
        frame_mini_btns.pack(pady=(5, 8))

        self._mini_pause_btn = tk.Button(frame_mini_btns, text="⏸ 暫停", bg="#cc8800", fg="#ffffff",
                  font=("Segoe UI", 9), bd=0, padx=8, pady=2,
                  command=self._toggle_pause, state="disabled")
        self._mini_pause_btn.pack(side="left", padx=(0, 5))
        self._mini_stop_btn = tk.Button(frame_mini_btns, text="⏹ 停止", bg="#aa3333", fg="#ffffff",
                  font=("Segoe UI", 9), bd=0, padx=8, pady=2,
                  command=self._stop_navigation, state="disabled")
        self._mini_stop_btn.pack(side="left", padx=(0, 5))
        tk.Button(frame_mini_btns, text="🔼 還原", bg="#444444", fg="#ffffff",
                  font=("Segoe UI", 9), bd=0, padx=8, pady=2,
                  command=self._exit_mini_mode).pack(side="left")

        # Hide main window
        self.root.withdraw()

        # Start updating mini status
        self._mini_update_running = True
        self._mini_update()

    def _exit_mini_mode(self):
        """Restore main window and close mini window."""
        self._mini_update_running = False
        if hasattr(self, '_mini_win') and self._mini_win.winfo_exists():
            self._mini_win.destroy()
        self.root.deiconify()
        self.root.state("zoomed")

    def _mini_update(self):
        """Periodically update mini window status."""
        if not hasattr(self, '_mini_win') or not self._mini_win.winfo_exists():
            return
        if not self._mini_update_running:
            return

        # Determine current state
        if self._paused:
            status = "⏸ 已暫停"
            color = "#ccaa00"
        elif self._running:
            status = "🌸 種花中..."
            color = "#88cc88"
        elif self._drifting:
            status = "📍 停留飄動中"
            color = "#88aaee"
        else:
            status = "⏸ 閒置"
            color = "#888888"

        self._mini_status.config(text=status, fg=color)

        # Enable/disable pause+stop buttons based on state
        if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
            btn_state = "normal" if (self._running or self._paused) else "disabled"
            self._mini_pause_btn.config(state=btn_state)
        if hasattr(self, '_mini_stop_btn') and self._mini_stop_btn.winfo_exists():
            btn_state = "normal" if (self._running or self._drifting or self._paused) else "disabled"
            self._mini_stop_btn.config(state=btn_state)

        # Show progress info
        info_lines = []
        try:
            speed = float(self.speed_var.get())
            info_lines.append(f"{speed:.1f} km/h")
        except (ValueError, tk.TclError):
            pass

        # Navigation progress
        if self._running and hasattr(self, '_nav_total_segs') and self._nav_total_segs > 0:
            # ETA based on current speed and remaining distance
            dist_done = getattr(self, '_nav_dist_done', 0)
            dist_total = getattr(self, '_nav_total_dist', 0)
            if dist_total > 0:
                dist_left = dist_total - dist_done
                try:
                    speed_mps = speed / 3.6
                    if speed_mps > 0:
                        eta_sec = dist_left / speed_mps
                        if eta_sec < 60:
                            eta_str = f"~{eta_sec:.0f}秒"
                        else:
                            eta_str = f"~{eta_sec/60:.1f}分"
                        info_lines.append(f"剩餘 {eta_str}")
                except Exception:
                    pass

        self._mini_info.config(text="  ".join(info_lines))

        # Schedule next update
        if self._mini_update_running:
            self._mini_win.after(1000, self._mini_update)

    # ── Full-mode navigation status updater ──

    def _start_nav_status_update(self):
        """Start periodic update of the navigation status label in full mode."""
        self._nav_status_updating = True
        self._update_nav_status()

    def _stop_nav_status_update(self):
        """Stop updating the navigation status label."""
        self._nav_status_updating = False
        if hasattr(self, '_nav_status_label'):
            self._nav_status_label.config(text="")

    def _update_nav_status(self):
        """Periodically refresh the full-mode navigation progress label."""
        if not getattr(self, '_nav_status_updating', False):
            return

        text = ""
        if self._paused:
            text = "⏸ 已暫停"
        elif self._running and self._nav_total_dist > 0:
            dist_done = self._nav_dist_done
            dist_total = self._nav_total_dist
            dist_left = dist_total - dist_done
            try:
                speed_kmh = float(self.speed_var.get())
                speed_mps = speed_kmh / 3.6
                if speed_mps > 0:
                    eta_sec = dist_left / speed_mps
                    if eta_sec < 60:
                        eta_str = f"~{eta_sec:.0f}秒"
                    else:
                        eta_str = f"~{eta_sec/60:.1f}分"
                    pct = (dist_done / dist_total * 100) if dist_total > 0 else 0
                    text = f"🌸 剩餘 {eta_str} | {dist_done:.0f}/{dist_total:.0f}m ({pct:.0f}%)"
            except (ValueError, tk.TclError):
                pass
        elif self._drifting:
            text = "📍 停留飄動中"

        if hasattr(self, '_nav_status_label'):
            self._nav_status_label.config(text=text)

        # Schedule next update
        if self._nav_status_updating:
            self.root.after(1000, self._update_nav_status)

    def _mini_drag_start(self, event):
        self._mini_dx = event.x_root
        self._mini_dy = event.y_root

    def _mini_drag_motion(self, event):
        if not hasattr(self, '_mini_win') or not self._mini_win.winfo_exists():
            return
        x = self._mini_win.winfo_x() + (event.x_root - self._mini_dx)
        y = self._mini_win.winfo_y() + (event.y_root - self._mini_dy)
        self._mini_win.geometry(f"+{x}+{y}")
        self._mini_dx = event.x_root
        self._mini_dy = event.y_root

    # ── Hand-Draw Route ──

    def _draw_start(self):
        """Enter draw mode: clicks/drags on map add waypoints."""
        self._draw_clear()  # Clear first (this resets _draw_mode to False)
        self._draw_last_drag_pos = None

        # Now set draw mode AFTER clear
        self._draw_mode = True

        method = self._draw_method.get()
        if method == "drag":
            self._draw_drag_mode = True
            self.btn_draw_start.config(text=" 拖曳畫線中... (按住左鍵拖曳)")
            self._log("[DRAW] 拖曳模式啟動，按住左鍵在地圖上拖曳畫線。")
            self._log("[DRAW] 提示：拖曳畫線時地圖不會移動，畫完按「生成路徑」恢復。")
            # Bind drag events to the map canvas
            if self.map_widget:
                self.map_widget.canvas.bind("<B1-Motion>", self._draw_on_drag, add=False)
                self.map_widget.canvas.bind("<ButtonRelease-1>", self._draw_on_drag_end, add=False)
        else:
            self._draw_drag_mode = False
            self.btn_draw_start.config(text=" 點擊加點中... (點地圖加點)")
            self._log("[DRAW] 點擊模式啟動，點擊地圖新增路徑點。")

    def _draw_on_drag(self, event):
        """Handle mouse drag on map canvas to sample points."""
        if not self._draw_mode or not self._draw_drag_mode:
            return
        if not self.map_widget:
            return

        # Convert canvas pixel to lat/lon using tkintermapview's built-in method
        try:
            coordinate = self.map_widget.convert_canvas_coords_to_decimal_coords(event.x, event.y)
            lat, lon = coordinate
        except Exception:
            return

        # Throttle: only add point if far enough from last one
        try:
            min_dist = float(self._draw_drag_interval.get())
        except (ValueError, tk.TclError):
            min_dist = 30

        if self._draw_last_drag_pos is not None:
            last_lat, last_lon = self._draw_last_drag_pos
            dist = haversine(last_lat, last_lon, lat, lon)
            if dist < min_dist:
                return  # Too close, skip

        self._draw_last_drag_pos = (lat, lon)
        self._draw_points.append((lat, lon))

        # Update path on map (no markers in drag mode to avoid clutter)
        self._draw_update_path()

        # Update UI
        count = len(self._draw_points)
        self._draw_info_label.config(text=f"路徑點: {count}")
        if count % 5 == 0:  # Only update listbox every 5 points to reduce lag
            self._draw_listbox.delete(0, "end")
            for i, (lt, ln) in enumerate(self._draw_points):
                self._draw_listbox.insert("end", f"  {i+1}. ({lt:.5f}, {ln:.5f})")

    def _draw_on_drag_end(self, event):
        """Handle mouse release after drag drawing."""
        if not self._draw_mode or not self._draw_drag_mode:
            return
        self._draw_last_drag_pos = None
        count = len(self._draw_points)
        # Final update of listbox
        self._draw_listbox.delete(0, "end")
        for i, (lt, ln) in enumerate(self._draw_points):
            self._draw_listbox.insert("end", f"  {i+1}. ({lt:.5f}, {ln:.5f})")
        self._log(f"[DRAW] 拖曳結束，共 {count} 點。按「生成路徑」完成。")

    def _draw_map_click(self, lat, lon):
        """Called when map is clicked in draw mode."""
        self._draw_points.append((lat, lon))
        idx = len(self._draw_points)

        # Add marker
        if self.map_widget:
            marker = self.map_widget.set_marker(lat, lon, text=f"{idx}",
                                                 marker_color_circle="purple",
                                                 marker_color_outside="darkviolet")
            self._draw_markers.append(marker)

        # Update path on map
        self._draw_update_path()

        # Update UI
        self._draw_info_label.config(text=f"路徑點: {idx}")
        self._draw_listbox.insert("end", f"  {idx}. ({lat:.6f}, {lon:.6f})")
        self._log(f"[DRAW] 新增點 {idx}: ({lat:.6f}, {lon:.6f})")

    def _draw_update_path(self):
        """Redraw the path line on map."""
        if self._draw_path:
            self._draw_path.delete()
            self._draw_path = None
        if len(self._draw_points) >= 2 and self.map_widget:
            self._draw_path = self.map_widget.set_path(
                self._draw_points, color="purple", width=3
            )

    def _draw_undo(self):
        """Remove the last waypoint."""
        if not self._draw_points:
            self._log("[DRAW] 沒有點可以撤回。")
            return
        self._draw_points.pop()
        if self._draw_markers:
            marker = self._draw_markers.pop()
            marker.delete()
        self._draw_update_path()
        # Update listbox
        self._draw_listbox.delete("end")
        self._draw_info_label.config(text=f"路徑點: {len(self._draw_points)}")
        self._log(f"[DRAW] 撤回，剩餘 {len(self._draw_points)} 點。")

    def _draw_clear(self):
        """Clear all draw points."""
        for marker in self._draw_markers:
            try:
                marker.delete()
            except Exception:
                pass
        self._draw_markers = []
        self._draw_points = []
        self._draw_last_drag_pos = None
        if self._draw_path:
            self._draw_path.delete()
            self._draw_path = None
        self._draw_listbox.delete(0, "end")
        self._draw_info_label.config(text="路徑點: 0")
        # If drag mode was active, restore map drag
        if self._draw_drag_mode and self.map_widget:
            self.map_widget.canvas.bind("<B1-Motion>", self.map_widget.mouse_move)
            self.map_widget.canvas.bind("<ButtonRelease-1>", self.map_widget.mouse_release)
        self._draw_drag_mode = False
        self._draw_mode = False
        self.btn_draw_start.config(text=" 開始畫路徑")

    def _draw_generate(self):
        """Generate route from drawn points. Optionally snap to road."""
        if len(self._draw_points) < 2:
            self._log("[DRAW] 至少需要 2 個點才能生成路徑。")
            return

        self._draw_mode = False
        self._draw_drag_mode = False
        self.btn_draw_start.config(text=" 開始畫路徑")

        # Restore map's original drag behavior by rebinding its handlers
        if self.map_widget:
            self.map_widget.canvas.bind("<B1-Motion>", self.map_widget.mouse_move)
            self.map_widget.canvas.bind("<ButtonRelease-1>", self.map_widget.mouse_release)

        if self._draw_snap_road.get():
            # Use road-snapping API (Valhalla/OSRM) to connect the dots via roads
            self._draw_generate_snapped()
        else:
            # Use points directly as the route (straight lines)
            self._route_coords = list(self._draw_points)
            self._draw_route_on_map()
            total_dist = sum(
                haversine(self._draw_points[i][0], self._draw_points[i][1],
                          self._draw_points[i+1][0], self._draw_points[i+1][1])
                for i in range(len(self._draw_points) - 1)
            )
            self._log(f"[DRAW] 直線路徑已生成！{len(self._route_coords)} 節點, {total_dist:.0f}m")
            # Set A/B from route
            self._set_point_a(self._route_coords[0][0], self._route_coords[0][1])
            if len(self._route_coords) > 1:
                last = self._route_coords[-1]
                self._set_point_c(last[0], last[1])

    def _draw_generate_snapped(self):
        """Snap drawn points to roads using Valhalla, then use as route."""
        if requests is None:
            self._log("[ERROR] pip install requests")
            return

        # Downsample: routing APIs reject too many waypoints (URL length / waypoint cap).
        # Keep endpoints, thin the middle so we stay under a safe limit.
        MAX_WAYPOINTS = 40
        raw_points = self._draw_points
        points = self._downsample_points(raw_points, MAX_WAYPOINTS)
        mode = self.route_mode_var.get()
        if len(points) < len(raw_points):
            self._log(f"[DRAW] 節點過多，已精簡 {len(raw_points)} → {len(points)} 點以避免 API 失敗。")
        self._log(f"[DRAW] 正在對齊道路 ({len(points)} 點, 模式={mode})...")

        def _do():
            try:
                import json as _json

                valhalla_mode_map = {"foot": "pedestrian", "bike": "bicycle", "driving": "auto"}
                costing = valhalla_mode_map.get(mode, "pedestrian")

                # Build waypoints for Valhalla
                locations = [{"lat": lat, "lon": lon} for lat, lon in points]
                payload = {
                    "locations": locations,
                    "costing": costing,
                    "shape_match": "map_snap",
                    "directions_options": {"units": "meters"}
                }
                valhalla_url = f"https://valhalla1.openstreetmap.de/route?json={_json.dumps(payload)}"
                resp = requests.get(valhalla_url, timeout=20)

                if resp.status_code == 200:
                    data = resp.json()
                    all_coords = []
                    total_distance = 0
                    for leg in data["trip"]["legs"]:
                        shape_encoded = leg["shape"]
                        leg_coords = self._decode_polyline(shape_encoded)
                        if all_coords and leg_coords:
                            leg_coords = leg_coords[1:]
                        all_coords.extend(leg_coords)
                        total_distance += leg["summary"]["length"] * 1000
                    self._route_coords = all_coords
                    self._log(f"[DRAW] 道路路徑已生成！{len(self._route_coords)} 節點, {total_distance:.0f}m")
                    if self.map_widget:
                        self.root.after(0, self._draw_route_on_map)
                    # Set A/C
                    if self._route_coords:
                        self.root.after(0, lambda: self._set_point_a(
                            self._route_coords[0][0], self._route_coords[0][1]))
                        self.root.after(0, lambda: self._set_point_c(
                            self._route_coords[-1][0], self._route_coords[-1][1]))
                    return

                # Fallback to OSRM
                self._log(f"[DRAW] Valhalla 失敗 (HTTP {resp.status_code})，嘗試 OSRM...")
                osrm_points = ";".join(f"{lon},{lat}" for lat, lon in points)
                osrm_url = (
                    f"http://router.project-osrm.org/route/v1/{mode}/"
                    f"{osrm_points}?overview=full&geometries=geojson"
                )
                resp = requests.get(osrm_url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "Ok" or not data.get("routes"):
                    self._log(f"[ERROR] OSRM: {data.get('code', 'unknown')}")
                    return
                geojson_coords = data["routes"][0]["geometry"]["coordinates"]
                self._route_coords = [(pt[1], pt[0]) for pt in geojson_coords]
                distance = data["routes"][0]["distance"]
                self._log(f"[DRAW] OSRM 路徑已生成！{len(self._route_coords)} 節點, {distance:.0f}m")
                if self.map_widget:
                    self.root.after(0, self._draw_route_on_map)
                if self._route_coords:
                    self.root.after(0, lambda: self._set_point_a(
                        self._route_coords[0][0], self._route_coords[0][1]))
                    self.root.after(0, lambda: self._set_point_c(
                        self._route_coords[-1][0], self._route_coords[-1][1]))
            except Exception as e:
                self._log(f"[ERROR] 手繪路徑生成失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    # ── Dark Mode ──

    def _toggle_dark_mode(self):
        dark = self._dark_mode.get()

        if HAS_SVTTK:
            sv_ttk.set_theme("dark" if dark else "light")
            # Update the log widget (ScrolledText is not managed by ttk theme)
            if dark:
                self.log.configure(bg="#1c1c1c", fg="#d4d4d4", insertbackground="#d4d4d4")
            else:
                self.log.configure(bg="white", fg="black", insertbackground="black")
        else:
            # Fallback: manual style (no sv_ttk)
            style = ttk.Style()
            if dark:
                bg = "#1e1e1e"
                fg = "#d4d4d4"
                entry_bg = "#2d2d2d"
                style.theme_use("default")
                style.configure(".", background=bg, foreground=fg, fieldbackground=entry_bg,
                               insertcolor=fg, bordercolor="#555555", darkcolor=bg, lightcolor=bg)
                style.configure("TLabel", background=bg, foreground=fg)
                style.configure("TFrame", background=bg)
                style.configure("TLabelframe", background=bg, foreground=fg)
                style.configure("TLabelframe.Label", background=bg, foreground=fg)
                style.configure("TButton", background="#3c3c3c", foreground=fg)
                style.configure("TCheckbutton", background=bg, foreground=fg, indicatorbackground=entry_bg)
                style.configure("TRadiobutton", background=bg, foreground=fg, indicatorbackground=entry_bg)
                style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
                style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg)
                style.configure("TScale", background=bg, troughcolor="#3c3c3c")
                style.configure("TPanedwindow", background=bg)
                style.configure("TNotebook", background=bg, bordercolor="#555555")
                style.configure("TNotebook.Tab", background="#3c3c3c", foreground=fg, padding=[8, 4])
                style.map("TNotebook.Tab",
                          background=[("selected", "#505050"), ("active", "#454545")],
                          foreground=[("selected", "#ffffff"), ("active", "#ffffff")])
                style.map("TButton", background=[("active", "#505050")])
                style.map("TCheckbutton", background=[("active", bg)])
                style.map("TRadiobutton", background=[("active", bg)])
                style.map("TCombobox", fieldbackground=[("readonly", entry_bg)])
                self.root.configure(bg=bg)
                self.log.configure(bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4")
            else:
                style.theme_use("vista")
                self.root.configure(bg="")
                self.log.configure(bg="white", fg="black", insertbackground="black")

        # Windows title bar dark/light mode (Windows 10 1809+)
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1 if dark else 0)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    # ── Map Click Handlers ──

    def _on_map_click(self, coords):
        lat, lon = coords

        # If in draw mode, add point to hand-drawn route instead
        if self._draw_mode:
            self._draw_map_click(lat, lon)
            return

        mode = self._click_mode.get()
        if mode == "A":
            self._set_point_a(lat, lon)
            self._click_mode.set("B")
        elif mode == "B":
            self._set_point_b(lat, lon)
            self._click_mode.set("C")
        else:
            self._set_point_c(lat, lon)
            self._click_mode.set("A")

    def _set_point_a_from_menu(self, coords):
        self._set_point_a(coords[0], coords[1])

    def _set_point_b_from_menu(self, coords):
        self._set_point_b(coords[0], coords[1])

    def _set_point_c_from_menu(self, coords):
        self._set_point_c(coords[0], coords[1])

    def _set_point_a(self, lat, lon):
        self.entry_a_lat.delete(0, "end")
        self.entry_a_lat.insert(0, f"{lat:.6f}")
        self.entry_a_lng.delete(0, "end")
        self.entry_a_lng.insert(0, f"{lon:.6f}")
        if self._marker_a:
            self._marker_a.delete()
        if self.map_widget:
            self._marker_a = self.map_widget.set_marker(lat, lon, text="A 起點",
                                                         marker_color_circle="green",
                                                         marker_color_outside="darkgreen")
        self._log(f"[MAP] 起點 A: ({lat:.6f}, {lon:.6f})")

    def _set_point_b(self, lat, lon):
        self.entry_b_lat.delete(0, "end")
        self.entry_b_lat.insert(0, f"{lat:.6f}")
        self.entry_b_lng.delete(0, "end")
        self.entry_b_lng.insert(0, f"{lon:.6f}")
        if self._marker_b:
            self._marker_b.delete()
        if self.map_widget:
            self._marker_b = self.map_widget.set_marker(lat, lon, text="B 經過",
                                                         marker_color_circle="blue",
                                                         marker_color_outside="darkblue")
        self._log(f"[MAP] 經過 B: ({lat:.6f}, {lon:.6f})")

    def _set_point_c(self, lat, lon):
        self.entry_c_lat.delete(0, "end")
        self.entry_c_lat.insert(0, f"{lat:.6f}")
        self.entry_c_lng.delete(0, "end")
        self.entry_c_lng.insert(0, f"{lon:.6f}")
        if self._marker_c:
            self._marker_c.delete()
        if self.map_widget:
            self._marker_c = self.map_widget.set_marker(lat, lon, text="C 終點",
                                                         marker_color_circle="red",
                                                         marker_color_outside="darkred")
        self._log(f"[MAP] 終點 C: ({lat:.6f}, {lon:.6f})")

    def _swap_ac(self):
        """Swap A and C coordinates."""
        a_lat = self.entry_a_lat.get()
        a_lng = self.entry_a_lng.get()
        c_lat = self.entry_c_lat.get()
        c_lng = self.entry_c_lng.get()
        if not c_lat.strip() or not c_lng.strip():
            self._log("[WARN] C 點未設定，無法交換。")
            return
        self.entry_a_lat.delete(0, "end")
        self.entry_a_lat.insert(0, c_lat)
        self.entry_a_lng.delete(0, "end")
        self.entry_a_lng.insert(0, c_lng)
        self.entry_c_lat.delete(0, "end")
        self.entry_c_lat.insert(0, a_lat)
        self.entry_c_lng.delete(0, "end")
        self.entry_c_lng.insert(0, a_lng)
        try:
            self._set_point_a(float(c_lat), float(c_lng))
            self._set_point_c(float(a_lat), float(a_lng))
        except ValueError:
            pass
        self._log("[MAP] A ⇅ C 已交換")

    # ── Logging ──

    def _log(self, msg):
        def _append():
            self.log.config(state="normal")
            self.log.insert("end", f"{msg}\n")
            self.log.see("end")
            self.log.config(state="disabled")
        self.root.after(0, _append)

    # ── Search Location ──

    def _search_location(self):
        query = self.entry_search.get().strip()
        if not query:
            return
        if requests is None:
            self._log("[ERROR] pip install requests")
            return

        def _apply_result(lat, lon, display):
            self._log(f"[SEARCH] {display}")
            self._log(f"         ({lat:.6f}, {lon:.6f})")
            if self.map_widget:
                self.root.after(0, lambda: self.map_widget.set_position(lat, lon))
                self.root.after(0, lambda: self.map_widget.set_zoom(16))
            mode = self._click_mode.get()
            if mode == "A":
                self.root.after(0, lambda: self._set_point_a(lat, lon))
            elif mode == "C":
                self.root.after(0, lambda: self._set_point_c(lat, lon))
            else:
                self.root.after(0, lambda: self._set_point_b(lat, lon))

        def _do():
            # Primary: OpenStreetMap Nominatim — supports any-language queries
            # (e.g. 台北101) and returns localized names via accept-language.
            # Requires an identifying User-Agent per its usage policy.
            headers = {"User-Agent": "PikminGPS/1.0 (github.com/kaoru12345/pikmin-gps-spoofer)"}
            try:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "jsonv2", "limit": 1, "accept-language": "zh-TW,zh,en"},
                    headers=headers, timeout=10,
                )
                resp.raise_for_status()
                results = resp.json()
                if results:
                    r = results[0]
                    lat, lon = float(r["lat"]), float(r["lon"])
                    _apply_result(lat, lon, r.get("display_name", query))
                    return
                self._log(f"[SEARCH] Nominatim 找不到，改用 Photon...")
            except Exception as e:
                self._log(f"[SEARCH] Nominatim 失敗 ({e})，改用 Photon...")

            # Fallback: Photon (Komoot). No 'lang=zh' — that param only accepts
            # en/de/fr/it and returns HTTP 400 otherwise.
            try:
                resp = requests.get(
                    "https://photon.komoot.io/api/",
                    params={"q": query, "limit": 1},
                    headers=headers, timeout=10,
                )
                resp.raise_for_status()
                features = resp.json().get("features", [])
                if not features:
                    self._log(f"[SEARCH] 找不到: {query}")
                    return
                feature = features[0]
                lon, lat = feature["geometry"]["coordinates"]
                props = feature.get("properties", {})
                display = " ".join(
                    p for p in (props.get("name", query), props.get("city", ""), props.get("country", "")) if p
                ).strip()
                _apply_result(lat, lon, display)
            except Exception as e:
                self._log(f"[ERROR] 搜尋失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    # ── Paste Coordinates ──

    def _paste_from_clipboard(self):
        """Grab text from clipboard and put it in the paste entry."""
        try:
            clip = self.root.clipboard_get().strip()
            self.entry_paste.delete(0, "end")
            self.entry_paste.insert(0, clip)
            self._paste_coords()
        except tk.TclError:
            self._log("[ERROR] 剪貼簿是空的。")

    def _paste_coords(self):
        """Parse 'lat, lon' from paste entry and set as A or B point."""
        text = self.entry_paste.get().strip()
        if not text:
            self._log("[ERROR] 請輸入或貼上座標。")
            return

        # Support formats: "lat, lon" / "lat,lon" / "lat lon"
        import re
        parts = re.split(r'[,\s]+', text.strip())
        if len(parts) < 2:
            self._log(f"[ERROR] 無法解析座標: {text}")
            return

        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError:
            self._log(f"[ERROR] 座標格式錯誤: {text}")
            return

        if self._click_mode.get() == "A":
            self._set_point_a(lat, lon)
        else:
            self._set_point_b(lat, lon)

        # Move map to the point
        if self.map_widget:
            self.map_widget.set_position(lat, lon)
            self.map_widget.set_zoom(15)

    # ── Teleport ──

    def _teleport_to_a(self):
        """Instantly set GPS to point A and drift around it."""
        try:
            lat = float(self.entry_a_lat.get())
            lon = float(self.entry_a_lng.get())
        except ValueError:
            self._log("[ERROR] A 點座標格式錯誤。")
            return

        # Stop any existing drift
        self._drifting = False
        time.sleep(0.1)  # Give drift loop time to exit

        # Move map to A point
        if self.map_widget:
            self.map_widget.set_position(lat, lon)
            self.map_widget.set_zoom(15)

        def _do():
            if not HAS_PMD3:
                self._log(f"[TELEPORT] (測試模式) 瞬移到 ({lat:.6f}, {lon:.6f})")
                return
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")
                gps.set_location(lat, lon)
                self._log(f"[TELEPORT] 已瞬移到 ({lat:.6f}, {lon:.6f})")
                # Start drifting
                self._drifting = True
                self._log("[DRIFT] 開始停留飄動（±3m），按「恢復真實 GPS」停止。")
                while self._drifting:
                    drift_lat = lat + random.gauss(0, 0.000015)
                    drift_lon = lon + random.gauss(0, 0.000015)
                    gps.set_location(drift_lat, drift_lon)
                    time.sleep(1.0)
            except Exception as e:
                self._log(f"[ERROR] 瞬移失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _release_gps(self):
        """Stop simulating and restore real GPS."""
        def _do():
            self._drifting = False
            self._running = False
            gps = iPhoneGPS.get_instance()
            if gps.connected:
                try:
                    gps.clear_location()
                except Exception:
                    pass
                self._log("[GPS] 已恢復真實定位。")
            else:
                self._log("[GPS] 目前沒有模擬定位。")
        threading.Thread(target=_do, daemon=True).start()

    # ── Flash Mode (撿彩虹盆栽) ──

    def _flash_mode(self):
        """
        Flash mode for picking up event seedlings:
        1. Inject GPS at target for a longer stabilization period
        2. Switch to low-frequency injection (every 3s) to appear more natural
        3. After the window, resume normal drift
        """
        try:
            lat = float(self.entry_a_lat.get())
            lon = float(self.entry_a_lng.get())
        except ValueError:
            self._log("[ERROR] A 點座標格式錯誤。")
            return

        # Stop any existing drift/nav
        self._drifting = False
        self._running = False

        def _do():
            if not HAS_PMD3:
                self._log("[FLASH] (測試模式) 模擬閃爍")
                return
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")

                # Step 1: Stabilize — inject for 5 seconds to let GPS chip lock on
                self._log(f"[FLASH] 穩定注入中 ({lat:.6f}, {lon:.6f})...")
                for i in range(5):
                    gps.set_location(lat, lon)
                    time.sleep(1.0)
                self._log("[FLASH] 座標已穩定。")

                # Step 2: Clear injection completely
                self._log("[FLASH] ⚡ 釋放 GPS — 可以操作！")
                gps.clear_location()

                # Step 3: Wait 3 seconds (pure GPS chip latency window)
                self._log("[FLASH]   釋放中 3s...")
                time.sleep(1.0)
                self._log("[FLASH]   釋放中 2s...")
                time.sleep(1.0)
                self._log("[FLASH]   釋放中 1s...")
                time.sleep(1.0)

                # Step 4: Single re-injection then resume normal drift
                self._log("[FLASH] ⚠ 注入！")
                drift_lat = lat + random.gauss(0, 0.000012)
                drift_lon = lon + random.gauss(0, 0.000012)
                gps.set_location(drift_lat, drift_lon)

                # Step 5: Back to normal drift
                self._log("[FLASH] 恢復正常飄動。")
                self._drifting = True
                while self._drifting:
                    drift_lat = lat + random.gauss(0, 0.000015)
                    drift_lon = lon + random.gauss(0, 0.000015)
                    gps.set_location(drift_lat, drift_lon)
                    time.sleep(1.0)

            except Exception as e:
                self._log(f"[ERROR] 閃爍模式失敗: {e}")

        threading.Thread(target=_do, daemon=True).start()

    # ── Screen Mirror ──

    def _toggle_screen_mirror(self):
        """Open a separate window showing iPhone screen via repeated screenshots."""
        if hasattr(self, '_mirror_window') and self._mirror_window and self._mirror_window.winfo_exists():
            self._mirror_running = False
            self._mirror_window.destroy()
            self._mirror_window = None
            self._log("[MIRROR] 已關閉螢幕投影。")
            return

        self._mirror_running = True
        self._mirror_window = tk.Toplevel(self.root)
        self._mirror_window.title("iPhone Screen")
        self._mirror_window.geometry("300x600")
        self._mirror_window.attributes("-topmost", True)
        self._mirror_window.resizable(True, True)
        self._mirror_window.protocol("WM_DELETE_WINDOW", self._close_mirror)

        self._mirror_label = tk.Label(self._mirror_window, bg="black")
        self._mirror_label.pack(fill="both", expand=True)

        self._log("[MIRROR] 開啟螢幕投影...")

        def _mirror_loop():
            try:
                from PIL import Image, ImageTk
                import io

                # Use existing GPS tunnel connection
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[MIRROR] 連接裝置: {info}")

                # Try DVT Screenshot first (iOS 17+/26+), fallback to ScreenshotService (older)
                screenshot_func = None
                try:
                    from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
                    screenshot_svc = Screenshot(gps._dvt)
                    gps._run_async(screenshot_svc.connect())
                    screenshot_func = lambda: gps._run_async(screenshot_svc.get_screenshot())
                    self._log("[MIRROR] 使用 DVT Screenshot")
                except (ImportError, Exception) as e1:
                    try:
                        from pymobiledevice3.services.screenshot import ScreenshotService
                        from pymobiledevice3.lockdown import create_using_usbmux
                        loop = asyncio.new_event_loop()
                        lockdown = loop.run_until_complete(create_using_usbmux(autopair=True))
                        svc = ScreenshotService(lockdown)
                        screenshot_func = lambda: loop.run_until_complete(svc.take_screenshot())
                        self._log("[MIRROR] 使用 ScreenshotService (舊版)")
                    except Exception as e2:
                        self._log(f"[MIRROR] 無法初始化截圖服務: {e2}")
                        self._mirror_running = False
                        return

                self._log(f"[MIRROR] 已連接，開始投影...")

                while self._mirror_running:
                    try:
                        png_data = screenshot_func()
                        img = Image.open(io.BytesIO(png_data))
                        # Resize to fit current window size
                        if self._mirror_window:
                            win_w = self._mirror_window.winfo_width()
                            win_h = self._mirror_window.winfo_height()
                            if win_w > 1 and win_h > 1:
                                # Maintain aspect ratio
                                img_w, img_h = img.size
                                ratio = min(win_w / img_w, win_h / img_h)
                                new_w = int(img_w * ratio)
                                new_h = int(img_h * ratio)
                                img = img.resize((new_w, new_h), Image.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        if self._mirror_running and self._mirror_window:
                            self._mirror_label.config(image=photo)
                            self._mirror_label.image = photo
                        time.sleep(0.05)
                    except Exception as e:
                        self._log(f"[MIRROR] 截圖失敗: {e}")
                        time.sleep(2.0)
            except Exception as e:
                self._log(f"[MIRROR] 連接失敗: {e}")
                self._mirror_running = False

        threading.Thread(target=_mirror_loop, daemon=True).start()

    def _close_mirror(self):
        self._mirror_running = False
        if hasattr(self, '_mirror_window') and self._mirror_window:
            self._mirror_window.destroy()
            self._mirror_window = None
        self._log("[MIRROR] 已關閉螢幕投影。")

    # ── Saved Locations ──

    def _refresh_saved_locations(self):
        locations = load_locations()
        category = self._loc_category.get()
        if category != "全部":
            filtered = [loc for loc in locations if loc.get("category", "純點") == category]
        else:
            filtered = locations
        self._filtered_locations = filtered
        names = [f"[{loc.get('category', '純點')}] {loc['name']} ({loc['lat']:.4f}, {loc['lon']:.4f})" for loc in filtered]
        self._loc_combo["values"] = names
        if names:
            self._loc_combo.current(0)
        else:
            self._loc_combo.set("")

    def _teleport_to_saved_loc(self):
        idx = self._loc_combo.current()
        filtered = getattr(self, '_filtered_locations', load_locations())
        if idx < 0 or idx >= len(filtered):
            self._log("[ERROR] 請選擇一個收藏地點。")
            return
        loc = filtered[idx]
        # Set as point A and teleport
        self._set_point_a(loc["lat"], loc["lon"])
        if self.map_widget:
            self.map_widget.set_position(loc["lat"], loc["lon"])
            self.map_widget.set_zoom(15)
        self._teleport_to_a()

    def _save_current_location(self):
        try:
            lat = float(self.entry_a_lat.get())
            lon = float(self.entry_a_lng.get())
        except ValueError:
            self._log("[ERROR] 請先設定 A 點座標。")
            return

        # Ask category
        category = self._loc_category.get()
        if category == "全部":
            category = "純點"

        name = simpledialog.askstring(
            "儲存地點",
            f"座標: ({lat:.6f}, {lon:.6f})\n分類: {category}\n請輸入地點名稱：",
            parent=self.root
        )
        if not name:
            return
        locations = load_locations()
        locations.append({"name": name, "lat": lat, "lon": lon, "category": category})
        save_locations(locations)
        self._refresh_saved_locations()
        self._log(f"[SAVE] 已儲存地點: [{category}] {name} ({lat:.6f}, {lon:.6f})")

    def _delete_saved_location(self):
        idx = self._loc_combo.current()
        filtered = getattr(self, '_filtered_locations', load_locations())
        if idx < 0 or idx >= len(filtered):
            self._log("[ERROR] 請選擇要刪除的地點。")
            return
        target = filtered[idx]
        # Remove from full list
        locations = load_locations()
        locations = [loc for loc in locations if not (loc["name"] == target["name"] and loc["lat"] == target["lat"] and loc["lon"] == target["lon"])]
        save_locations(locations)
        self._refresh_saved_locations()
        self._log(f"[DELETE] 已刪除地點: {target['name']}")

    def _change_location_category(self):
        idx = self._loc_combo.current()
        filtered = getattr(self, '_filtered_locations', load_locations())
        if idx < 0 or idx >= len(filtered):
            self._log("[ERROR] 請選擇要改分類的地點。")
            return
        target = filtered[idx]

        # Show category selection dialog
        categories = ["純點", "菇點", "明信片點", "我的最愛", "活動點"]
        current_cat = target.get("category", "純點")

        dialog = tk.Toplevel(self.root)
        dialog.title("變更分類")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Dark mode support
        dark = self._dark_mode.get()
        bg = "#1e1e1e" if dark else "SystemButtonFace"
        fg = "#d4d4d4" if dark else "black"
        dialog.configure(bg=bg)

        lbl = tk.Label(dialog, text=f"地點: {target['name']}\n目前分類: {current_cat}", bg=bg, fg=fg)
        lbl.pack(pady=5)

        selected = tk.StringVar(value=current_cat)
        for cat in categories:
            rb = ttk.Radiobutton(dialog, text=cat, variable=selected, value=cat)
            rb.pack(anchor="w", padx=20)

        # Auto size and center on parent
        dialog.update_idletasks()
        w = dialog.winfo_reqwidth() + 40
        h = dialog.winfo_reqheight() + 40
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        def _apply():
            new_cat = selected.get()
            locations = load_locations()
            for loc in locations:
                if loc["name"] == target["name"] and loc["lat"] == target["lat"] and loc["lon"] == target["lon"]:
                    loc["category"] = new_cat
                    break
            save_locations(locations)
            self._refresh_saved_locations()
            self._log(f"[EDIT] {target['name']} 分類改為: {new_cat}")
            dialog.destroy()

        ttk.Button(dialog, text="確定", command=_apply).pack(pady=10)

        # Re-calculate size after button is added
        dialog.update_idletasks()
        w = dialog.winfo_reqwidth() + 40
        h = dialog.winfo_reqheight() + 20
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

    # ── Saved Routes ──

    def _refresh_saved_routes(self):
        routes = load_routes()
        names = [f"{r['name']} ({r['distance']}m)" for r in routes]
        self._route_combo["values"] = names
        if names:
            self._route_combo.current(0)

    def _load_saved_route(self):
        idx = self._route_combo.current()
        routes = load_routes()
        if idx < 0 or idx >= len(routes):
            self._log("[ERROR] 請選擇一個收藏路徑。")
            return
        route = routes[idx]
        self._route_coords = [(pt[0], pt[1]) for pt in route["coords"]]
        # Set A/B from route
        self._set_point_a(self._route_coords[0][0], self._route_coords[0][1])
        self._set_point_b(self._route_coords[-1][0], self._route_coords[-1][1])
        self._draw_route_on_map()
        self._log(f"[LOAD] 已載入路徑: {route['name']} (節點={len(self._route_coords)}, {route['distance']}m)")

    def _save_current_route(self):
        if not self._route_coords:
            self._log("[ERROR] 沒有路徑可儲存，請先抓取路徑。")
            return
        name = simpledialog.askstring("儲存路徑", "路徑名稱：", parent=self.root)
        if not name:
            return
        distance = sum(
            haversine(self._route_coords[i][0], self._route_coords[i][1],
                      self._route_coords[i+1][0], self._route_coords[i+1][1])
            for i in range(len(self._route_coords) - 1)
        )
        routes = load_routes()
        routes.append({
            "name": name,
            "distance": int(distance),
            "coords": [[lat, lon] for lat, lon in self._route_coords],
        })
        save_routes(routes)
        self._refresh_saved_routes()
        self._log(f"[SAVE] 已儲存路徑: {name} ({int(distance)}m, {len(self._route_coords)} 節點)")

    def _delete_saved_route(self):
        idx = self._route_combo.current()
        routes = load_routes()
        if idx < 0 or idx >= len(routes):
            self._log("[ERROR] 請選擇要刪除的路徑。")
            return
        removed = routes.pop(idx)
        save_routes(routes)
        self._refresh_saved_routes()
        self._log(f"[DELETE] 已刪除路徑: {removed['name']}")

    # ── Export / Import ──

    def _export_data(self):
        """Export locations and routes to a single JSON file."""
        from tkinter import filedialog
        filepath = filedialog.asksaveasfilename(
            parent=self.root,
            title="匯出資料",
            defaultextension=".json",
            filetypes=[("JSON 檔案", "*.json")],
            initialfile="pikmin_data_export.json"
        )
        if not filepath:
            return
        data = {
            "locations": load_locations(),
            "routes": load_routes()
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._log(f"[EXPORT] 已匯出到: {filepath}")
        self._log(f"         地點: {len(data['locations'])} 筆, 路徑: {len(data['routes'])} 筆")

    def _import_data(self):
        """Import locations and routes from a JSON file."""
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            parent=self.root,
            title="匯入資料",
            filetypes=[("JSON 檔案", "*.json")]
        )
        if not filepath:
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._log(f"[ERROR] 匯入失敗: {e}")
            return

        # Ask merge or replace
        result = messagebox.askyesnocancel(
            "匯入資料",
            "要合併到現有資料嗎？\n\n是 = 合併（保留現有 + 加入新的）\n否 = 取代（清除現有，用匯入的）\n取消 = 不匯入",
            parent=self.root
        )
        if result is None:
            return

        imported_locs = data.get("locations", [])
        imported_routes = data.get("routes", [])

        if result:  # Merge
            existing_locs = load_locations()
            existing_routes = load_routes()
            existing_locs.extend(imported_locs)
            existing_routes.extend(imported_routes)
            save_locations(existing_locs)
            save_routes(existing_routes)
            self._log(f"[IMPORT] 已合併匯入: 地點 +{len(imported_locs)}, 路徑 +{len(imported_routes)}")
        else:  # Replace
            save_locations(imported_locs)
            save_routes(imported_routes)
            self._log(f"[IMPORT] 已取代匯入: 地點 {len(imported_locs)} 筆, 路徑 {len(imported_routes)} 筆")

        self._refresh_saved_locations()
        self._refresh_saved_routes()

    # ── Developer Mode ──

    def _enable_dev_mode(self):
        """One-click: reveal Developer Mode toggle and guide user."""
        if not HAS_PMD3:
            self._log("[ERROR] pymobiledevice3 未安裝。")
            return

        def _do():
            try:
                import asyncio
                from pymobiledevice3.lockdown import create_using_usbmux
                from pymobiledevice3.services.amfi import AmfiService

                loop = asyncio.new_event_loop()
                lockdown = loop.run_until_complete(create_using_usbmux(autopair=True))
                self._log(f"[DEVICE] 已連接 {lockdown.product_type} iOS {lockdown.product_version}")

                # Check if already enabled
                dev_mode = loop.run_until_complete(
                    lockdown.get_value(domain="com.apple.security.mac.amfi", key="DeveloperModeStatus")
                )
                if dev_mode:
                    self._log("[OK] 開發者模式已經開啟，可以直接使用！")
                    return

                # Reveal the toggle in Settings
                amfi = AmfiService(lockdown)
                loop.run_until_complete(amfi.reveal_developer_mode_option_in_ui())
                self._log("[OK] 開發者模式選項已顯示！")
                self._log("[ACTION] 請到 iPhone: 設定 → 隱私權與安全性 → 開發者模式 → 打開")
                self._log("[ACTION] 手機會重開機，重開後再確認一次「開啟」")
                self._log("[ACTION] 完成後重新插 USB 即可使用。")
            except Exception as e:
                self._log(f"[ERROR] {e}")

        threading.Thread(target=_do, daemon=True).start()

    # ── Route Fetching (OSRM) ──

    def _fetch_route(self):
        if requests is None:
            self._log("[ERROR] pip install requests")
            return
        try:
            a_lat = float(self.entry_a_lat.get())
            a_lng = float(self.entry_a_lng.get())
            b_lat = float(self.entry_b_lat.get())
            b_lng = float(self.entry_b_lng.get())
        except ValueError:
            self._log("[ERROR] 座標格式錯誤。")
            return

        # Check if C point is set (C is the final destination, B becomes a waypoint)
        c_lat = None
        c_lng = None
        try:
            c_lat_str = self.entry_c_lat.get().strip()
            c_lng_str = self.entry_c_lng.get().strip()
            if c_lat_str and c_lng_str:
                c_lat = float(c_lat_str)
                c_lng = float(c_lng_str)
        except ValueError:
            pass

        # Build waypoints: A → B(waypoint) → C(destination), or A → B(destination) if no C
        if c_lat is not None and c_lng is not None:
            waypoints = [{"lat": a_lat, "lon": a_lng}, {"lat": b_lat, "lon": b_lng}, {"lat": c_lat, "lon": c_lng}]
            route_desc = "A→B→C"
        else:
            waypoints = [{"lat": a_lat, "lon": a_lng}, {"lat": b_lat, "lon": b_lng}]
            route_desc = "A→B"
        self._log(f"[ROUTE] 正在抓取路徑 ({route_desc})...")

        # Map route mode to OSRM profile
        mode = self.route_mode_var.get()

        def _do_fetch():
            try:
                import json as _json

                # Valhalla (free, hosted by OpenStreetMap.de) — proper foot/bike/driving profiles
                valhalla_mode_map = {"foot": "pedestrian", "bike": "bicycle", "driving": "auto"}
                costing = valhalla_mode_map.get(mode, "pedestrian")

                payload = {
                    "locations": waypoints,
                    "costing": costing,
                    "shape_match": "map_snap",
                    "directions_options": {"units": "meters"}
                }
                valhalla_url = f"https://valhalla1.openstreetmap.de/route?json={_json.dumps(payload)}"
                resp = requests.get(valhalla_url, timeout=15)

                if resp.status_code == 200:
                    data = resp.json()
                    # Combine all legs into one route
                    all_coords = []
                    total_distance = 0
                    total_duration = 0
                    for leg in data["trip"]["legs"]:
                        shape_encoded = leg["shape"]
                        leg_coords = self._decode_polyline(shape_encoded)
                        if all_coords and leg_coords:
                            # Skip first point of subsequent legs (same as last of previous)
                            leg_coords = leg_coords[1:]
                        all_coords.extend(leg_coords)
                        total_distance += leg["summary"]["length"] * 1000
                        total_duration += leg["summary"]["time"]
                    self._route_coords = all_coords
                    self._log(f"[ROUTE] 成功！({mode}, {route_desc}) 節點={len(self._route_coords)}, 距離={total_distance:.0f}m, 時間={total_duration:.0f}s")
                    if self.map_widget:
                        self.root.after(0, self._draw_route_on_map)
                    return

                # Fallback to OSRM
                self._log(f"[ROUTE] Valhalla 不可用 (HTTP {resp.status_code})，改用 OSRM...")
                # OSRM waypoints format: lon,lat;lon,lat;lon,lat
                osrm_points = ";".join(f"{wp['lon']},{wp['lat']}" for wp in waypoints)
                osrm_url = (
                    f"http://router.project-osrm.org/route/v1/{mode}/"
                    f"{osrm_points}"
                    f"?overview=full&geometries=geojson"
                )
                resp = requests.get(osrm_url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "Ok" or not data.get("routes"):
                    self._log(f"[ERROR] OSRM: {data.get('code', 'unknown')}")
                    return
                geojson_coords = data["routes"][0]["geometry"]["coordinates"]
                self._route_coords = [(pt[1], pt[0]) for pt in geojson_coords]
                distance = data["routes"][0]["distance"]
                duration = data["routes"][0]["duration"]
                self._log(f"[OSRM] 成功！節點={len(self._route_coords)}, 距離={distance:.0f}m, 時間={duration:.0f}s")
                if self.map_widget:
                    self.root.after(0, self._draw_route_on_map)
            except Exception as e:
                self._log(f"[ERROR] 路徑抓取失敗: {e}")

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _draw_route_on_map(self):
        if self._route_path:
            self._route_path.delete()
        if self._route_coords and self.map_widget:
            self._route_path = self.map_widget.set_path(
                [(lat, lon) for lat, lon in self._route_coords], color="blue", width=3
            )

    @staticmethod
    def _downsample_points(points, max_points):
        """Reduce a list of (lat, lon) to at most max_points, keeping the
        first and last and evenly sampling the middle. Preserves route shape
        well enough for road-snapping while staying under API waypoint limits."""
        n = len(points)
        if n <= max_points or max_points < 2:
            return list(points)
        # Always keep first and last; evenly pick (max_points - 2) from the middle.
        result = [points[0]]
        step = (n - 1) / (max_points - 1)
        for i in range(1, max_points - 1):
            idx = round(i * step)
            result.append(points[idx])
        result.append(points[-1])
        # Drop consecutive duplicates that rounding may introduce
        deduped = [result[0]]
        for pt in result[1:]:
            if pt != deduped[-1]:
                deduped.append(pt)
        return deduped

    @staticmethod
    def _decode_polyline(encoded):
        """Decode Valhalla's encoded polyline (precision 6) to list of (lat, lon)."""
        coords = []
        i = 0
        lat = 0
        lon = 0
        while i < len(encoded):
            for coord in range(2):
                shift = 0
                result = 0
                while True:
                    byte = ord(encoded[i]) - 63
                    i += 1
                    result |= (byte & 0x1F) << shift
                    shift += 5
                    if byte < 0x20:
                        break
                if result & 1:
                    result = ~(result >> 1)
                else:
                    result >>= 1
                if coord == 0:
                    lat += result
                else:
                    lon += result
            coords.append((lat / 1e6, lon / 1e6))
        return coords

    def _clear_route(self):
        """Clear the current route from map and memory."""
        self._route_coords = []
        if self._route_path:
            self._route_path.delete()
            self._route_path = None
        self._log("[ROUTE] 已清除目前路徑。")

    # ── Navigation ──

    def _start_navigation(self):
        if self._running:
            self._log("[WARN] 導航已在進行中。")
            return
        if not self._route_coords:
            self._log("[ERROR] 請先抓取路徑。")
            return
        # Stop drifting if active
        self._drifting = False
        try:
            speed_kmh = float(self.speed_var.get())
        except (ValueError, tk.TclError):
            self._log("[ERROR] 時速格式錯誤。")
            return

        jitter = self.var_jitter.get()
        self._running = True
        self._thread = threading.Thread(
            target=self._navigation_worker,
            args=(list(self._route_coords), jitter),
            daemon=True,
        )
        self._thread.start()
        self._log(f"[NAV] 開始導航！時速={speed_kmh:.1f} km/h, Jitter={'ON' if jitter else 'OFF'}")
        self._log("[NAV] 可隨時拖動滑桿調整時速，即時生效。")

    def _stop_navigation(self):
        if self._running or self._drifting:
            self._running = False
            self._drifting = False
            self._paused = False
            self.btn_pause.config(text=" 暫停")
            if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
                self._mini_pause_btn.config(text="⏸ 暫停")
            self._stop_nav_status_update()
            self._log("[NAV] 停止中...")
        else:
            self._log("[WARN] 沒有正在進行的導航。")

    def _toggle_pause(self):
        """Toggle pause: freeze in place, resume when pressed again."""
        if not self._running and not self._paused:
            self._log("[WARN] 沒有正在進行的導航可暫停。")
            return

        if self._paused:
            # Resume
            self._paused = False
            self.btn_pause.config(text=" 暫停")
            if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
                self._mini_pause_btn.config(text="⏸ 暫停", bg="#cc8800")
            self._log("[NAV] ▶ 繼續導航！可重新插上 USB。")
        else:
            # Pause
            self._paused = True
            self.btn_pause.config(text=" 繼續")
            if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
                self._mini_pause_btn.config(text="▶ 繼續", bg="#339933")
            self._log("[NAV] ⏸ 已暫停，GPS 停在原地。可以拔 USB。")

    def _start_spiral(self):
        """Start spiral walking around point A — expanding circles that don't overlap."""
        if self._running:
            self._log("[WARN] 導航已在進行中。")
            return
        try:
            center_lat = float(self.entry_a_lat.get())
            center_lon = float(self.entry_a_lng.get())
        except ValueError:
            self._log("[ERROR] A 點座標格式錯誤。")
            return

        # Stop drifting if active
        self._drifting = False
        jitter = self.var_jitter.get()
        self._running = True
        self._thread = threading.Thread(
            target=self._spiral_worker,
            args=(center_lat, center_lon, jitter),
            daemon=True,
        )
        self._thread.start()
        self._log(f"[SPIRAL] 開始繞圈種花，中心=({center_lat:.6f}, {center_lon:.6f})")
        self._log("[SPIRAL] 按「停止移動」結束。")

    def _spiral_worker(self, center_lat, center_lon, jitter):
        """Walk in expanding spiral around center point."""
        gps = None

        if HAS_PMD3:
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")
                else:
                    self._log("[DEVICE] 使用現有連線")
            except Exception as e:
                self._log(f"[DEVICE] 連接失敗: {e}")
                self._log("[DEVICE] 進入測試模式。")
                gps = None
        else:
            self._log("[DEVICE] pymobiledevice3 未安裝，測試模式。")

        # Spiral parameters optimized for Pikmin Bloom flower planting:
        # - Coverage per pass: ~30x25m (6x5 grid of 5m cells)
        # - Cooldown per cell: ~5 minutes
        # - Moving diagonally/at angle covers more fresh cells
        # Start radius: 40m (clear of the initial drift area)
        # Expand by 25m each circle (just outside the 30m width coverage)
        # Walk at ~45° angle offset each circle for better grid coverage
        radius = 40.0  # meters, starting radius
        radius_increment = 25.0  # meters per full circle (> 15m half-width, avoids overlap)
        points_per_circle = 72  # one point every 5 degrees = smooth + good cell coverage
        angle_offset = 0.0  # rotate starting angle each circle for diagonal sweep
        tick = 0
        circle_count = 0
        spiral_start_time = time.time()

        try:
            while self._running:
                circle_count += 1
                self._log(f"[SPIRAL] 圈 {circle_count}，半徑={radius:.0f}m")

                for i in range(points_per_circle):
                    if not self._running:
                        break

                    # Read dynamic speed
                    try:
                        speed_kmh = float(self.speed_var.get())
                    except (ValueError, tk.TclError):
                        speed_kmh = 10.0
                    fluctuation = random.uniform(-1.5, 1.5)
                    actual_kmh = max(1.0, speed_kmh + fluctuation)

                    # Calculate position on circle with angle offset for diagonal sweep
                    angle = angle_offset + (i / points_per_circle) * 2 * math.pi
                    # Convert radius (meters) to lat/lon offset
                    dlat = (radius * math.cos(angle)) / 111320.0
                    dlon = (radius * math.sin(angle)) / (111320.0 * math.cos(math.radians(center_lat)))

                    lat = center_lat + dlat
                    lon = center_lon + dlon

                    if jitter:
                        lat += random.gauss(0, 0.000008)
                        lon += random.gauss(0, 0.000008)

                    tick += 1

                    if gps:
                        try:
                            gps.set_location(lat, lon)
                        except Exception as e:
                            self._log(f"[WARN] GPS 注入失敗: {e}")
                            self._log("[RETRY] 等待重新連線...")
                            try:
                                gps.disconnect()
                            except Exception:
                                pass
                            iPhoneGPS._instance = None
                            retry_ok = False
                            for attempt in range(150):
                                if not self._running:
                                    break
                                time.sleep(2)
                                try:
                                    gps = iPhoneGPS.get_instance()
                                    gps.connect()
                                    gps.set_location(lat, lon)
                                    self._log("[RETRY] 重連成功！")
                                    retry_ok = True
                                    break
                                except Exception:
                                    if attempt % 5 == 0:
                                        self._log(f"[RETRY] 等待中... ({attempt*2}s)")
                                    try:
                                        gps.disconnect()
                                    except Exception:
                                        pass
                                    iPhoneGPS._instance = None
                            if not retry_ok:
                                if self._running:
                                    self._log("[RETRY] 重連失敗，自動暫停。重新插好 USB 後按「繼續」即可恢復。")
                                    self._paused = True
                                    self.root.after(0, lambda: self.btn_pause.config(text=" 繼續"))
                                    if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
                                        self.root.after(0, lambda: self._mini_pause_btn.config(text="▶ 繼續", bg="#339933"))
                                    # Wait in pause until user resumes or stops
                                    while self._paused and self._running:
                                        time.sleep(0.5)
                                    # After resume, try reconnect before continuing
                                    if self._running:
                                        try:
                                            gps = iPhoneGPS.get_instance()
                                            gps.connect()
                                            gps.set_location(lat, lon)
                                            self._log("[RETRY] 恢復後重連成功！繼續導航。")
                                        except Exception:
                                            self._log("[ERROR] 恢復後仍無法連線，導航停止。")
                                            self._running = False
                                            break

                    if tick % 3 == 0 or tick == 1:
                        self.root.after(0, lambda la=lat, lo=lon: self._update_current_marker(la, lo))

                    if tick % 10 == 0 or tick == 1:
                        self._log(f"  圈{circle_count} [{i+1}/{points_per_circle}] ({lat:.6f}, {lon:.6f}) {actual_kmh:.1f} km/h")

                    # Sleep based on speed: circumference segment / speed = time per point
                    seg_length = (2 * math.pi * radius) / points_per_circle
                    sleep_time = seg_length / (actual_kmh / 3.6)
                    time.sleep(max(0.5, min(sleep_time, 3.0)))

                    # Pause loop
                    while self._paused and self._running:
                        time.sleep(0.5)

                # Expand radius and rotate angle offset for next circle
                # 45° offset ensures diagonal sweep hits fresh grid cells
                radius += radius_increment
                angle_offset += math.pi / 4  # rotate 45° each circle

                # After 5 minutes of walking, reset to starting radius
                # (cells from the first circle have cooled down by then)
                elapsed = time.time() - spiral_start_time
                if radius > 500 or elapsed > 300:
                    radius = 40.0
                    angle_offset += math.pi / 6  # slight extra offset on reset for variety
                    circle_count = 0
                    spiral_start_time = time.time()
                    self._log("[SPIRAL] 5 分鐘已到，格子已冷卻，從頭開始繞！")

        finally:
            self._running = False
            if self._current_marker:
                self.root.after(0, self._remove_current_marker)
            self.root.after(0, self._stop_nav_status_update)
            self._log("[SPIRAL] 繞圈結束。")

    def _navigation_worker(self, coords, jitter):
        gps = None

        if HAS_PMD3:
            try:
                gps = iPhoneGPS.get_instance()
                if not gps.connected:
                    info = gps.connect()
                    self._log(f"[DEVICE] 已連接 {info}")
                else:
                    self._log(f"[DEVICE] 使用現有連線")
            except Exception as e:
                self._log(f"[DEVICE] 連接失敗: {e}")
                self._log("[DEVICE] 進入測試模式（僅日誌）。")
                gps = None
        else:
            self._log("[DEVICE] pymobiledevice3 未安裝，測試模式。")

        # Walk through route segments dynamically reading speed each tick
        seg_idx = 0
        seg_progress = 0.0  # progress within current segment (0.0 to 1.0)
        total_segs = len(coords) - 1
        tick = 0

        # Calculate total route distance for ETA
        total_route_dist = sum(
            haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
            for i in range(total_segs)
        )
        # Navigation progress tracking (for mini mode)
        self._nav_seg_idx = 0
        self._nav_total_segs = total_segs
        self._nav_total_dist = total_route_dist
        self._nav_dist_done = 0.0
        self._nav_start_time = time.time()

        # Start full-mode progress display
        self.root.after(0, self._start_nav_status_update)

        try:
            while seg_idx < total_segs and self._running:
                lat1, lon1 = coords[seg_idx]
                lat2, lon2 = coords[seg_idx + 1]
                seg_dist = haversine(lat1, lon1, lat2, lon2)

                if seg_dist < 0.01:
                    seg_idx += 1
                    continue

                # Read current speed from slider (dynamic!)
                try:
                    speed_kmh = float(self.speed_var.get())
                except (ValueError, tk.TclError):
                    speed_kmh = 10.0

                # Add fluctuation
                fluctuation = random.uniform(-1.5, 1.5)
                actual_kmh = max(1.0, speed_kmh + fluctuation)
                actual_mps = actual_kmh / 3.6

                # How far we move this tick (1 second)
                move_dist = actual_mps
                move_ratio = move_dist / seg_dist

                seg_progress += move_ratio

                if seg_progress >= 1.0:
                    # Crossed into next segment
                    seg_idx += 1
                    seg_progress = 0.0
                    continue

                # Interpolate position
                lat = lat1 + (lat2 - lat1) * seg_progress
                lon = lon1 + (lon2 - lon1) * seg_progress

                if jitter:
                    lat += random.gauss(0, 0.000008)
                    lon += random.gauss(0, 0.000008)

                tick += 1

                # Update navigation progress for mini mode
                self._nav_seg_idx = seg_idx
                self._nav_dist_done = sum(
                    haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
                    for i in range(seg_idx)
                ) + seg_dist * seg_progress

                if gps:
                    try:
                        gps.set_location(lat, lon)
                    except Exception as e:
                        self._log(f"[WARN] GPS 注入失敗: {e}")
                        self._log("[RETRY] 等待重新連線（可拔插 USB）...")
                        # Disconnect old connection
                        try:
                            gps.disconnect()
                        except Exception:
                            pass
                        iPhoneGPS._instance = None
                        retry_ok = False
                        for attempt in range(150):  # Try for up to 5 minutes
                            if not self._running:
                                break
                            time.sleep(2)
                            try:
                                gps = iPhoneGPS.get_instance()
                                gps.connect()
                                gps.set_location(lat, lon)
                                self._log(f"[RETRY] 重連成功！從目前位置繼續導航。")
                                retry_ok = True
                                break
                            except Exception as retry_e:
                                if attempt % 5 == 0:
                                    self._log(f"[RETRY] 等待中... ({attempt*2}s)")
                                # Reset for next attempt
                                try:
                                    gps.disconnect()
                                except Exception:
                                    pass
                                iPhoneGPS._instance = None
                        if not retry_ok:
                            if self._running:
                                self._log("[RETRY] 重連失敗，自動暫停。重新插好 USB 後按「繼續」即可恢復。")
                                self._paused = True
                                self.root.after(0, lambda: self.btn_pause.config(text=" 繼續"))
                                if hasattr(self, '_mini_pause_btn') and self._mini_pause_btn.winfo_exists():
                                    self.root.after(0, lambda: self._mini_pause_btn.config(text="▶ 繼續", bg="#339933"))
                                # Wait in pause until user resumes or stops
                                while self._paused and self._running:
                                    time.sleep(0.5)
                                # After resume, try reconnect before continuing
                                if self._running:
                                    try:
                                        gps = iPhoneGPS.get_instance()
                                        gps.connect()
                                        gps.set_location(lat, lon)
                                        self._log("[RETRY] 恢復後重連成功！繼續導航。")
                                    except Exception:
                                        self._log("[ERROR] 恢復後仍無法連線，導航停止。")
                                        self._running = False
                                        break

                if tick % 3 == 0 or tick == 1:
                    self.root.after(0, lambda la=lat, lo=lon: self._update_current_marker(la, lo))

                if tick % 5 == 0 or tick == 1:
                    self._log(f"  [{seg_idx+1}/{total_segs}] ({lat:.6f}, {lon:.6f}) {actual_kmh:.1f} km/h")

                time.sleep(1.0)

                # Pause loop: freeze here until resumed or stopped
                while self._paused and self._running:
                    time.sleep(0.5)

            if self._running:
                # Set final point
                if coords and gps:
                    gps.set_location(coords[-1][0], coords[-1][1])
                self._log("[NAV] 導航完成！已到達終點。")
            else:
                self._log("[NAV] 導航已停止。")
        finally:
            self._running = False
            # Don't disconnect — keep connection alive for reuse (teleport/next nav)
            if self._current_marker:
                self.root.after(0, self._remove_current_marker)
            self.root.after(0, self._stop_nav_status_update)

    def _update_current_marker(self, lat, lon):
        if not self.map_widget:
            return
        if self._current_marker:
            self._current_marker.delete()
        self._current_marker = self.map_widget.set_marker(
            lat, lon, text="📍", marker_color_circle="orange", marker_color_outside="darkorange"
        )

    def _remove_current_marker(self):
        if self._current_marker:
            self._current_marker.delete()
            self._current_marker = None

    # ── Session Save/Restore ──

    def _restore_session(self):
        """Restore last session's map position, A/B points, speed."""
        state = load_state()
        if not state:
            return
        # Restore A point
        if "a_lat" in state and "a_lng" in state:
            self.entry_a_lat.delete(0, "end")
            self.entry_a_lat.insert(0, str(state["a_lat"]))
            self.entry_a_lng.delete(0, "end")
            self.entry_a_lng.insert(0, str(state["a_lng"]))
        # Restore B point
        if "b_lat" in state and "b_lng" in state:
            self.entry_b_lat.delete(0, "end")
            self.entry_b_lat.insert(0, str(state["b_lat"]))
            self.entry_b_lng.delete(0, "end")
            self.entry_b_lng.insert(0, str(state["b_lng"]))
        # Restore speed
        if "speed" in state:
            self.speed_var.set(state["speed"])
        # Restore map position
        if self.map_widget and "map_lat" in state and "map_lng" in state:
            self.map_widget.set_position(state["map_lat"], state["map_lng"])
            if "map_zoom" in state:
                self.map_widget.set_zoom(state["map_zoom"])
        # Restore jitter
        if "jitter" in state:
            self.var_jitter.set(state["jitter"])
        # Restore dark mode
        if state.get("dark_mode"):
            self._dark_mode.set(True)
            self._toggle_dark_mode()

    def _on_close(self):
        """Save session state and exit."""
        # Stop any active loops
        self._drifting = False
        self._running = False
        # Clean up GPS connection
        if HAS_PMD3:
            gps = iPhoneGPS.get_instance()
            if gps.connected:
                try:
                    gps.clear_location()
                    gps.disconnect()
                except Exception:
                    pass
        try:
            state = {
                "a_lat": self.entry_a_lat.get(),
                "a_lng": self.entry_a_lng.get(),
                "b_lat": self.entry_b_lat.get(),
                "b_lng": self.entry_b_lng.get(),
                "speed": self.speed_var.get(),
                "jitter": self.var_jitter.get(),
                "dark_mode": self._dark_mode.get(),
            }
            if self.map_widget:
                pos = self.map_widget.get_position()
                state["map_lat"] = pos[0]
                state["map_lng"] = pos[1]
                state["map_zoom"] = self.map_widget.zoom
            save_state(state)
        except Exception:
            pass
        self.root.destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    if HAS_SVTTK:
        sv_ttk.set_theme("dark")
    app = GPSSpoofApp(root)
    root.mainloop()
