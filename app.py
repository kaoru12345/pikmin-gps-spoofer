"""
Pikmin Bloom / Pokémon GO — USB GPS Spoofer with Auto-Navigation
Embedded map (tkintermapview) for click-to-set A/B points.
Uses iOS 17+ CoreDevice tunnel + DVT LocationSimulation API.
Requires: pymobiledevice3, requests, tkintermapview, iTunes (for usbmuxd)
Platform: Windows (Tkinter GUI)
"""

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
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
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

        # Global font scaling 1.5x
        import tkinter.font as tkfont
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=int(default_font.cget("size") * 1.5))
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(size=int(text_font.cget("size") * 1.5))
        fixed_font = tkfont.nametofont("TkFixedFont")
        fixed_font.configure(size=int(fixed_font.cget("size") * 1.5))

        self._running = False
        self._drifting = False
        self._thread = None
        self._route_coords = []
        self._marker_a = None
        self._marker_b = None
        self._marker_c = None
        self._route_path = None
        self._current_marker = None
        self._click_mode = tk.StringVar(value="A")

        self._build_ui()
        self._restore_session()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ── Main paned layout: left=(map+log), right=controls ──
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Left: Map + Log (vertical split) ──
        frame_left = ttk.Frame(self.root)
        paned.add(frame_left, weight=10)

        frame_map = ttk.LabelFrame(frame_left, text="地圖 (左鍵點擊設定 A/B 點，右鍵選單)", padding=5)
        frame_map.pack(fill="both", expand=True)

        if tkintermapview:
            self.map_widget = tkintermapview.TkinterMapView(frame_map, width=600, height=500)
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

        # ── Log (under map) ──
        frame_log = ttk.LabelFrame(frame_left, text="狀態日誌", padding=5)
        frame_log.pack(fill="both", expand=False, pady=(5, 0))

        self.log = scrolledtext.ScrolledText(frame_log, height=8, state="disabled", font=("Consolas", 13))
        self.log.pack(fill="both", expand=True)

        # ── Right: Controls ──
        frame_right = ttk.Frame(self.root)
        paned.add(frame_right, weight=1)

        # ── Click mode ──
        frame_mode = ttk.LabelFrame(frame_right, text="點擊模式", padding=5)
        frame_mode.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Radiobutton(frame_mode, text="設定起點 A", variable=self._click_mode, value="A").pack(side="left", padx=10)
        ttk.Radiobutton(frame_mode, text="設定經過 B", variable=self._click_mode, value="B").pack(side="left", padx=10)
        ttk.Radiobutton(frame_mode, text="設定終點 C", variable=self._click_mode, value="C").pack(side="left", padx=10)

        # ── Search ──
        frame_search = ttk.LabelFrame(frame_right, text="搜尋地點", padding=5)
        frame_search.pack(fill="x", padx=5, pady=(0, 5))

        self.entry_search = ttk.Entry(frame_search, width=15)
        self.entry_search.pack(side="left", padx=(0, 5))
        self.entry_search.bind("<Return>", lambda e: self._search_location())

        ttk.Button(frame_search, text="搜尋", width=5, command=self._search_location).pack(side="left")

        # ── Paste Coordinates (蘑菇地圖格式: "lat, lon") ──
        frame_paste = ttk.LabelFrame(frame_right, text="貼上座標 (格式: lat, lon)", padding=5)
        frame_paste.pack(fill="x", padx=5, pady=(0, 5))

        self.entry_paste = ttk.Entry(frame_paste, width=15)
        self.entry_paste.pack(side="left", padx=(0, 5))
        self.entry_paste.bind("<Return>", lambda e: self._paste_coords())

        ttk.Button(frame_paste, text="設定", width=5, command=self._paste_coords).pack(side="left")
        ttk.Button(frame_paste, text="📋", width=3, command=self._paste_from_clipboard).pack(side="left", padx=2)

        # ── Input Frame ──
        frame_input = ttk.LabelFrame(frame_right, text="路徑設定", padding=10)
        frame_input.pack(fill="x", padx=5, pady=(0, 5))

        ttk.Label(frame_input, text="起點 A (lat, lng):").grid(row=0, column=0, sticky="w")
        self.entry_a_lat = ttk.Entry(frame_input, width=9)
        self.entry_a_lat.grid(row=0, column=1, padx=2)
        self.entry_a_lat.insert(0, "35.6812")
        self.entry_a_lng = ttk.Entry(frame_input, width=9)
        self.entry_a_lng.grid(row=0, column=2, padx=2)
        self.entry_a_lng.insert(0, "139.7671")

        ttk.Label(frame_input, text="經過 B (lat, lng):").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.entry_b_lat = ttk.Entry(frame_input, width=9)
        self.entry_b_lat.grid(row=1, column=1, padx=2, pady=(5, 0))
        self.entry_b_lat.insert(0, "35.6895")
        self.entry_b_lng = ttk.Entry(frame_input, width=9)
        self.entry_b_lng.grid(row=1, column=2, padx=2, pady=(5, 0))
        self.entry_b_lng.insert(0, "139.6917")

        ttk.Label(frame_input, text="終點 C (lat, lng):").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.entry_c_lat = ttk.Entry(frame_input, width=9)
        self.entry_c_lat.grid(row=2, column=1, padx=2, pady=(5, 0))
        self.entry_c_lng = ttk.Entry(frame_input, width=9)
        self.entry_c_lng.grid(row=2, column=2, padx=2, pady=(5, 0))

        ttk.Button(frame_input, text="⇅ A↔C", width=6, command=self._swap_ac).grid(row=0, column=3, rowspan=3, padx=5, sticky="ns")

        ttk.Label(frame_input, text="時速 (km/h):").grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.speed_var = tk.DoubleVar(value=10.0)
        self.entry_speed = ttk.Entry(frame_input, width=8, textvariable=self.speed_var)
        self.entry_speed.grid(row=3, column=1, sticky="w", padx=2, pady=(5, 0))

        self.speed_scale = ttk.Scale(frame_input, from_=1, to=20, variable=self.speed_var,
                                      orient="horizontal", length=120)
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

        # ── Buttons ──
        frame_btn = ttk.Frame(frame_right, padding=5)
        frame_btn.pack(fill="x", padx=5)

        self.btn_devmode = ttk.Button(frame_btn, text="🔧 一鍵開啟開發者模式", command=self._enable_dev_mode)
        self.btn_devmode.pack(fill="x", pady=2)

        frame_tp = ttk.Frame(frame_btn)
        frame_tp.pack(fill="x", pady=2)
        frame_tp.columnconfigure(0, weight=1, uniform="btn")
        frame_tp.columnconfigure(1, weight=1, uniform="btn")
        self.btn_teleport = ttk.Button(frame_tp, text="⚡ 瞬移到 A 點", command=self._teleport_to_a)
        self.btn_teleport.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_release = ttk.Button(frame_tp, text="📍 恢復真實 GPS", command=self._release_gps)
        self.btn_release.grid(row=0, column=1, sticky="we", padx=(2, 0))

        # Flash mode button
        self.btn_flash = ttk.Button(frame_btn, text="🌈 閃爍模式 (撿盆栽用)", command=self._flash_mode)
        self.btn_flash.pack(fill="x", pady=2)

        # Screen mirror button
        self.btn_mirror = ttk.Button(frame_btn, text="📱 手機螢幕投影", command=self._toggle_screen_mirror)
        self.btn_mirror.pack(fill="x", pady=2)

        frame_fetch_btns = ttk.Frame(frame_btn)
        frame_fetch_btns.pack(fill="x", pady=2)
        frame_fetch_btns.columnconfigure(0, weight=1, uniform="btn")
        frame_fetch_btns.columnconfigure(1, weight=1, uniform="btn")
        self.btn_fetch = ttk.Button(frame_fetch_btns, text="🗺 抓取道路路徑", command=self._fetch_route)
        self.btn_fetch.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_clear_route = ttk.Button(frame_fetch_btns, text="🗑 清除路徑", command=self._clear_route)
        self.btn_clear_route.grid(row=0, column=1, sticky="we", padx=(2, 0))

        frame_route_btns = ttk.Frame(frame_btn)
        frame_route_btns.pack(fill="x", pady=2)
        frame_route_btns.columnconfigure(0, weight=1, uniform="btn")
        frame_route_btns.columnconfigure(1, weight=1, uniform="btn")
        self.btn_start = ttk.Button(frame_route_btns, text="🌸 開始自動種花", command=self._start_navigation)
        self.btn_start.grid(row=0, column=0, sticky="we", padx=(0, 2))
        self.btn_stop = ttk.Button(frame_route_btns, text="⏹ 停止移動", command=self._stop_navigation)
        self.btn_stop.grid(row=0, column=1, sticky="we", padx=(2, 0))

        self.btn_spiral = ttk.Button(frame_btn, text="🌀 A 點繞圈種花", command=self._start_spiral)
        self.btn_spiral.pack(fill="x", pady=2)

        # ── Saved Locations ──
        frame_loc = ttk.LabelFrame(frame_right, text="收藏地點", padding=5)
        frame_loc.pack(fill="x", padx=5, pady=(5, 0))

        self._loc_var = tk.StringVar()
        self._loc_combo = ttk.Combobox(frame_loc, textvariable=self._loc_var, state="readonly", width=18)
        self._loc_combo.pack(side="left", padx=(0, 5))

        ttk.Button(frame_loc, text="飛", width=3, command=self._teleport_to_saved_loc).pack(side="left", padx=2)
        ttk.Button(frame_loc, text="+", width=3, command=self._save_current_location).pack(side="left", padx=2)
        ttk.Button(frame_loc, text="✕", width=3, command=self._delete_saved_location).pack(side="left", padx=2)

        # ── Saved Routes ──
        frame_routes = ttk.LabelFrame(frame_right, text="收藏路徑", padding=5)
        frame_routes.pack(fill="x", padx=5, pady=(5, 0))

        self._route_var = tk.StringVar()
        self._route_combo = ttk.Combobox(frame_routes, textvariable=self._route_var, state="readonly", width=18)
        self._route_combo.pack(side="left", padx=(0, 5))

        ttk.Button(frame_routes, text="載入", width=4, command=self._load_saved_route).pack(side="left", padx=2)
        ttk.Button(frame_routes, text="+", width=3, command=self._save_current_route).pack(side="left", padx=2)
        ttk.Button(frame_routes, text="✕", width=3, command=self._delete_saved_route).pack(side="left", padx=2)

        self._refresh_saved_locations()
        self._refresh_saved_routes()

        # ── Dark Mode Toggle ──
        self._dark_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame_right, text="🌙 深色模式", variable=self._dark_mode,
                        command=self._toggle_dark_mode).pack(fill="x", padx=5, pady=(10, 0))

    # ── Dark Mode ──

    def _toggle_dark_mode(self):
        dark = self._dark_mode.get()
        style = ttk.Style()

        if dark:
            bg = "#1e1e1e"
            fg = "#d4d4d4"
            entry_bg = "#2d2d2d"

            # Windows title bar dark mode (Windows 10 1809+)
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
            style.map("TButton", background=[("active", "#505050")])
            style.map("TCheckbutton", background=[("active", bg)])
            style.map("TRadiobutton", background=[("active", bg)])
            style.map("TCombobox", fieldbackground=[("readonly", entry_bg)])
            self.root.configure(bg=bg)
            self.log.configure(bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4")
        else:
            # Windows title bar light mode
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(ctypes.c_int(0)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

            # Reset to clean light theme
            style.theme_use("vista")
            self.root.configure(bg="")
            self.log.configure(bg="white", fg="black", insertbackground="black")

    # ── Map Click Handlers ──

    def _on_map_click(self, coords):
        lat, lon = coords
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

        def _do():
            try:
                # Use Photon (Komoot) — better search quality than Nominatim, no API key
                url = "https://photon.komoot.io/api/"
                params = {"q": query, "limit": 1, "lang": "zh"}
                headers = {"User-Agent": "PikminGPS/1.0"}
                resp = requests.get(url, params=params, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                features = data.get("features", [])
                if not features:
                    self._log(f"[SEARCH] 找不到: {query}")
                    return
                feature = features[0]
                lon, lat = feature["geometry"]["coordinates"]
                props = feature.get("properties", {})
                name = props.get("name", query)
                city = props.get("city", "")
                country = props.get("country", "")
                display = f"{name} {city} {country}".strip()
                self._log(f"[SEARCH] {display}")
                self._log(f"         ({lat:.6f}, {lon:.6f})")
                # Move map to result
                if self.map_widget:
                    self.root.after(0, lambda: self.map_widget.set_position(lat, lon))
                    self.root.after(0, lambda: self.map_widget.set_zoom(16))
                # Set as A or B depending on current mode
                mode = self._click_mode.get()
                if mode == "A":
                    self.root.after(0, lambda: self._set_point_a(lat, lon))
                elif mode == "C":
                    self.root.after(0, lambda: self._set_point_c(lat, lon))
                else:
                    self.root.after(0, lambda: self._set_point_b(lat, lon))
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
        self._mirror_window.geometry("400x720")
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
                        # Resize to fit window maintaining aspect ratio
                        w, h = img.size
                        new_h = 720
                        new_w = int(w * new_h / h)
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
        names = [f"{loc['name']} ({loc['lat']:.4f}, {loc['lon']:.4f})" for loc in locations]
        self._loc_combo["values"] = names
        if names:
            self._loc_combo.current(0)

    def _teleport_to_saved_loc(self):
        idx = self._loc_combo.current()
        locations = load_locations()
        if idx < 0 or idx >= len(locations):
            self._log("[ERROR] 請選擇一個收藏地點。")
            return
        loc = locations[idx]
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
        name = simpledialog.askstring(
            "儲存地點",
            f"已擷取 A 點座標 ({lat:.6f}, {lon:.6f})\n請輸入地點名稱：",
            parent=self.root
        )
        if not name:
            return
        locations = load_locations()
        locations.append({"name": name, "lat": lat, "lon": lon})
        save_locations(locations)
        self._refresh_saved_locations()
        self._log(f"[SAVE] 已儲存地點: {name} ({lat:.6f}, {lon:.6f})")

    def _delete_saved_location(self):
        idx = self._loc_combo.current()
        locations = load_locations()
        if idx < 0 or idx >= len(locations):
            self._log("[ERROR] 請選擇要刪除的地點。")
            return
        removed = locations.pop(idx)
        save_locations(locations)
        self._refresh_saved_locations()
        self._log(f"[DELETE] 已刪除地點: {removed['name']}")

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
            self._log("[NAV] 停止中...")
        else:
            self._log("[WARN] 沒有正在進行的導航。")

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
                                self._log("[ERROR] 5 分鐘內未能重連，導航停止。")
                            self._running = False
                            break

                if tick % 3 == 0 or tick == 1:
                    self.root.after(0, lambda la=lat, lo=lon: self._update_current_marker(la, lo))

                if tick % 5 == 0 or tick == 1:
                    self._log(f"  [{seg_idx+1}/{total_segs}] ({lat:.6f}, {lon:.6f}) {actual_kmh:.1f} km/h")

                time.sleep(1.0)

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
    app = GPSSpoofApp(root)
    root.mainloop()
