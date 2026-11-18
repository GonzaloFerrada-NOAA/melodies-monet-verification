"""
Build the air-quality model verification dashboard: ONE self-contained html
with a Leaflet map, per-station Plotly time series and two reference layers:
HMS fire detections, VIIRS AOD and TEMPO AOD (hourly grids embedded as PNGs).

Settings live in config.yaml. Run:
    python generate_dashboard.py config.yaml [--viirs-file F] [--tempo-file F]
--viirs-file / --tempo-file: hourly AOD NetCDF of this cycle; left out or None = no layer.
"""
import argparse
import base64
import glob
import json
import math
import os
import re
import struct
import sys
import zlib
from collections import Counter

import numpy as np
import pandas as pd
import yaml

# =========================================================================
# 1. SETTINGS
# =========================================================================
def file_arg(s):
    """'None', 'null' or '' on the command line -> None (no file for this layer)."""
    return None if s is None or s.strip().lower() in ("", "none", "null") else s


ap = argparse.ArgumentParser(description="Build the model verification dashboard (one html).")
ap.add_argument("config", nargs="?", default="config.yaml", help="yaml settings (default: config.yaml)")
ap.add_argument("--viirs-file", type=file_arg, default=None,
                help="VIIRS AOD NetCDF with all hours of the cycle; omit or None = no VIIRS layer")
ap.add_argument("--tempo-file", type=file_arg, default=None,
                help="TEMPO AOD NetCDF with all hours of the cycle; omit or None = no TEMPO layer")
ARGS = ap.parse_args()
CFG_FILE = ARGS.config
with open(CFG_FILE) as f:
    cfg = yaml.safe_load(f)

CYCLE = pd.Timestamp(cfg["cycle"])
WIN_START = CYCLE - pd.Timedelta(hours=cfg["window_hours_before"])
WIN_END = CYCLE + pd.Timedelta(hours=cfg["window_hours_after"])
# HMS fires are now a reference layer, not a platform: drop a leftover 'type: fires' entry
PLATFORMS = {p: pc for p, pc in cfg["platforms"].items() if pc.get("type") != "fires"}
for p in set(cfg["platforms"]) - set(PLATFORMS):
    print(f"  note: platform '{p}' (type: fires) ignored; HMS fires are under 'reference' now")
REF_CFG = cfg.get("reference") or {}
DECIMALS = cfg.get("decimals") or {}
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.30.0.min.js"


def decimals_for(var):
    """Decimal places for a variable (config 'decimals', else its 'default')."""
    return int(DECIMALS.get(var, DECIMALS.get("default", 2)))


def rnd(v, nd):
    """Round to nd decimals; NaN/None -> None (JSON null = gap in the plot)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    r = round(float(v), nd)
    return int(r) if r.is_integer() else r


def js_literal(obj):
    """Compact JSON that is also safe inside <script> (no '</script>' breakouts)."""
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


# Discrete colour scales from config.yaml. Bound i = lower edge of bin i. A variable
# uses its own 'colors' if given, otherwise the first len(bounds) default colours.
color_scales = {}
cs = cfg.get("color_scales") or {}
default_colors = cs.get("default_colors", [])
for var, spec in (cs.get("variables") or {}).items():
    bounds = [float(b) for b in spec["bounds"]]
    if bounds != sorted(bounds):
        sys.exit(f"color_scales: bounds for '{var}' must be increasing")
    if "colors" in spec:
        colors = spec["colors"]
        if len(colors) != len(bounds):
            sys.exit(f"color_scales: '{var}' has {len(bounds)} bounds but {len(colors)} colors (need one per bound)")
    else:
        if len(bounds) > len(default_colors):
            sys.exit(f"color_scales: '{var}' has {len(bounds)} bounds but only {len(default_colors)} "
                     f"default colors; give it its own 'colors' list")
        colors = default_colors[:len(bounds)]
    color_scales[str(var)] = {"bounds": bounds, "colors": list(colors)}
print(f"Colour scales for: {', '.join(color_scales) or 'none'}")

# =========================================================================
# 2. STATION CSVs  ->  {platform: {station_id: {meta, data: {species: ...}}}}
# =========================================================================
# MELODIES-MONET writes one CSV per site:
#   <PLOT_OUTPUT_DIR>/<species>/<platform>/plot_grp1.timeseries.<species>.<start>.<end>.site[id].<site name>.csv
# columns: time, [time_local], siteid, latitude, longitude, <species> (= observation),
#          <MODEL>_<YYYYMMDD>-<HH>Z (one column per model run)
MODEL_COL = re.compile(r"^(?P<model>.+)_(?P<init>\d{8})-(?P<hh>\d{2})Z$")
SITE_NAME = re.compile(r"\.site(?:id)?\.(?P<name>.+)\.csv$")
PLAT_ALIAS = cfg.get("platform_aliases") or {}       # workflow folder name -> dashboard platform
SPEC_ALIAS = cfg.get("species_aliases") or {}        # workflow folder name -> dashboard variable

platforms_db = {p: {} for p in PLATFORMS}
model_runs = {}            # column -> {"model", "init" (Timestamp), "has_data"}
skipped = Counter()

for fpath in sorted(glob.glob(cfg["paths"]["station_csvs"], recursive=True)):
    wf_species = os.path.basename(os.path.dirname(os.path.dirname(fpath)))
    wf_plat = os.path.basename(os.path.dirname(fpath))
    plat = PLAT_ALIAS.get(wf_plat, wf_plat)
    species = SPEC_ALIAS.get(wf_species, wf_species)
    if plat not in platforms_db:
        skipped[f"platform '{wf_plat}' not in config"] += 1
        continue
    df = pd.read_csv(fpath)
    if df.empty or wf_species not in df.columns or "time" not in df.columns:
        skipped["empty or without an observation column"] += 1
        continue
    m = SITE_NAME.search(os.path.basename(fpath))
    name = m.group("name") if m else os.path.basename(fpath)[:-4]
    sid = str(df["siteid"].iloc[0]) if "siteid" in df.columns else name
    lat, lon = df["latitude"].dropna(), df["longitude"].dropna()
    if lat.empty or lon.empty:
        skipped["no latitude/longitude"] += 1
        continue
    st = platforms_db[plat].setdefault(sid, {
        "name": name, "lat": round(float(lat.iloc[0]), 4), "lon": round(float(lon.iloc[0]), 4), "data": {}})
    nd = decimals_for(species)
    series = {"Observation": [rnd(x, nd) for x in df[wf_species].tolist()]}
    for c in df.columns:
        mm = MODEL_COL.match(c)
        if not mm:
            continue                                # time_local, siteid, latitude, ...
        series[c] = [rnd(x, nd) for x in df[c].tolist()]
        run = model_runs.setdefault(c, {"model": mm["model"], "has_data": False,
                                        "init": pd.Timestamp(f"{mm['init']} {mm['hh']}:00")})
        run["has_data"] |= bool(df[c].notna().any())
    st["data"][species] = {
        "time": pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "series": series,
    }
for why, n in skipped.items():
    print(f"  skipped {n} CSV files: {why}")
print(f"Model runs found: {len(model_runs)} ({sum(not r['has_data'] for r in model_runs.values())} without any data)")

def platform_payload(stations):
    """Compact data for one platform.

    species:  {sp: {columns: [...], time: [...]}}  -> column order + shared time axis
    stations: {id: {name, lat, lon, obs_max: {sp: max}, d: {sp: {v: [[col0], [col1], ...], t?: [...]}}}}
    A station only carries its own 't' when its time axis differs from the shared one.
    """
    species_meta = {}
    for st in stations.values():
        for sp, d in st["data"].items():
            m = species_meta.setdefault(sp, {"columns": [], "times": Counter()})
            m["columns"] += [c for c in d["series"] if c not in m["columns"]]
            m["times"][tuple(d["time"])] += 1
    for m in species_meta.values():
        if "Observation" in m["columns"]:           # Observation always first
            m["columns"].remove("Observation")
            m["columns"].insert(0, "Observation")
        m["time"] = list(m.pop("times").most_common(1)[0][0])

    out = {}
    for sid, st in stations.items():
        e = {"name": st["name"], "lat": st["lat"], "lon": st["lon"], "obs_max": {}, "d": {}}
        for sp, d in st["data"].items():
            entry = {"v": [d["series"].get(c) for c in species_meta[sp]["columns"]]}
            if d["time"] != species_meta[sp]["time"]:
                entry["t"] = d["time"]
            e["d"][sp] = entry
            obs = [x for x in d["series"].get("Observation", []) if x is not None]
            e["obs_max"][sp] = max(obs) if obs else None
        out[sid] = e
    # Model runs with at least one value for each variable (models panel greys out the rest)
    for sp, m in species_meta.items():
        m["avail"] = [c for k, c in enumerate(m["columns"])
                      if any(st["d"].get(sp) and st["d"][sp]["v"][k] and
                             any(x is not None for x in st["d"][sp]["v"][k]) for st in out.values())]
    return {"species": species_meta, "stations": out}


# =========================================================================
# 3. HMS FIRE DETECTIONS (reference only, not compared against models)
#    One entry per unique (Lon, Lat) inside the window. FRP time series only
#    for GOES fires seen more than once (geostationary = fixed grid).
# =========================================================================
HMS_COLS = ["Lon", "Lat", "YearDay", "Time", "Satellite", "Method", "Ecosystem", "FRP"]
GAP_MIN = cfg["hms"]["gap_minutes"]
FRP_DEC = decimals_for("FRP")

days = pd.date_range(WIN_START.normalize(), (WIN_END - pd.Timedelta(seconds=1)).normalize(), freq="D")
hms_files = [cfg["paths"]["hms_fires"].format(date=d) for d in days]
for f in hms_files:
    print(f"  HMS file {'found  ' if os.path.exists(f) else 'MISSING'}: {f}")
hms_list = [pd.read_csv(f, names=HMS_COLS, skiprows=1, header=None, skipinitialspace=True)
            for f in hms_files if os.path.exists(f)]

# Column arrays (index i = one fire location, sorted small -> large FRP)
fires = {"lon": [], "lat": [], "sat": [], "frp": [], "n": [], "m": [], "eco": [],
         "t": [],              # minutes after t0 of the (first) detection; null = has a series
         "methods": [],
         "t0": WIN_START.strftime("%Y-%m-%dT%H:%M:00Z"),
         "minutes": int((WIN_END - WIN_START) / pd.Timedelta(minutes=1))}
fire_series = {}               # i -> {"t": [minutes after t0 (null point = gap)], "frp": [...]}
if hms_list:
    hms = pd.concat(hms_list, ignore_index=True).drop_duplicates()
    hms["Satellite"] = hms["Satellite"].str.strip()
    hms["Method"] = hms["Method"].str.strip()
    hms["FRP"] = hms["FRP"].where(hms["FRP"] >= 0)          # -999 (e.g. ANALYSIS) -> NaN
    # YearDay (YYYYDDD) + Time (HHMM) -> UTC; a daily file holds a few next-day detections
    hms["t"] = (pd.to_datetime(hms["YearDay"].astype(str), format="%Y%j")
                + pd.to_timedelta(hms["Time"] // 100, unit="h")
                + pd.to_timedelta(hms["Time"] % 100, unit="m"))
    hms = hms[(hms["t"] >= WIN_START) & (hms["t"] < WIN_END)].sort_values("t")
    hms["tmin"] = ((hms["t"] - WIN_START) / pd.Timedelta(minutes=1)).astype(int)

    groups = [(g["FRP"].max(), lon, lat, g) for (lon, lat), g in hms.groupby(["Lon", "Lat"])]
    groups.sort(key=lambda x: (-1 if pd.isna(x[0]) else x[0]))   # biggest drawn last (on top)

    for i, (fmax, lon, lat, g) in enumerate(groups):
        sat, method, n = g["Satellite"].iloc[0], g["Method"].iloc[0], len(g)
        has_ts = sat.startswith("GOES") and n > 1
        if has_ts:
            tt, ff, prev = [], [], None
            for tm, v in zip(g["tmin"], g["FRP"]):
                if prev is not None and tm - prev > GAP_MIN:     # null point = line break
                    tt.append(int(prev) + 1)
                    ff.append(None)
                tt.append(int(tm))
                ff.append(rnd(v, FRP_DEC))
                prev = tm
            fire_series[i] = {"t": tt, "frp": ff}
        if method not in fires["methods"]:
            fires["methods"].append(method)
        fires["lon"].append(round(float(lon), 4))
        fires["lat"].append(round(float(lat), 4))
        fires["sat"].append(sat)
        fires["frp"].append(rnd(fmax, FRP_DEC))
        fires["n"].append(int(n))
        fires["m"].append(fires["methods"].index(method))
        fires["eco"].append(int(g["Ecosystem"].iloc[0]))
        fires["t"].append(None if has_ts else int(g["tmin"].iloc[0]))

print(f"HMS: {len(fires['lon'])} fire locations between {WIN_START} and {WIN_END} "
      f"({len(fire_series)} with time series)")

# =========================================================================
# 3c. SATELLITE AOD: VIIRS and TEMPO (reference only, not compared against models)
#     One NetCDF file per product, given on the command line (--viirs-file,
#     --tempo-file; missing or None = no layer), with hourly grids:
#        lon(lon), lat(lat), time(time) "hours since ...", aod550(time, lat, lon), count
#     The two products have different grids; each keeps its own.
#     Every hour of the window with data + the count-weighted mean of them are
#     quantized to one byte per cell and stored as lossless 8-bit grayscale PNGs
#     on the NATIVE lat/lon grid (row 0 = north):
#        code 0      -> missing (drawn fully transparent)
#        code 1..255 -> AOD = vmax * ((code - 1) / 254)^2
#     The square-root spacing gives ~0.005 steps near AOD 0.1 and ~0.04 near 5.
#     The page stretches the rows onto Web Mercator itself, and reads the
#     hovered value straight from the native grid.
# =========================================================================
def png_gray8(a):
    """Lossless 8-bit grayscale PNG of a 2-D uint8 array, with numpy + zlib only."""
    h, w = a.shape
    a16, prev, rows = a.astype(np.int16), np.zeros(w, np.int16), []
    for r in range(h):
        cur = a16[r]
        # PNG row filters 0 = None, 1 = Sub (minus left), 2 = Up (minus row above)
        cand = (cur, np.concatenate(([cur[0]], cur[1:] - cur[:-1])), cur - prev)
        # libpng's heuristic: smallest sum of |residual| (as signed bytes)
        k = min(range(3), key=lambda f: int(np.abs((cand[f] + 128) % 256 - 128).sum()))
        rows.append(bytes([k]) + (cand[k] % 256).astype(np.uint8).tobytes())
        prev = cur

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def quantize(aod, vmax):
    """float AOD (NaN = missing) -> uint8 codes (0 = missing)."""
    q = np.zeros(aod.shape, np.uint8)
    ok = np.isfinite(aod)
    q[ok] = 1 + np.rint(254 * np.sqrt(np.clip(aod[ok], 0, vmax) / vmax)).astype(np.uint8)
    return q


def png_b64(aod, vmax):
    return base64.b64encode(png_gray8(quantize(aod, vmax))).decode()


def nc_times(var):
    """CF time variable ('<unit> since <date>') -> list of pandas Timestamps."""
    unit, ref = var.units.split(" since ")
    step = {"days": "D", "day": "D", "hours": "h", "hour": "h", "minutes": "min",
            "minute": "min", "seconds": "s", "second": "s"}[unit.strip().lower()]
    t0 = pd.Timestamp(ref.strip()).tz_localize(None)
    return [(t0 + pd.to_timedelta(float(v), unit=step)).round("min") for v in np.ma.filled(var[:], np.nan)]


def block_sum(x, f):
    """Sum over f x f blocks (array already trimmed to a multiple of f)."""
    ny, nx = x.shape
    return x.reshape(ny // f, f, nx // f, f).sum(axis=(1, 3))


def load_aod(path, acfg, name):
    """Hourly frames + mean of one AOD product -> page payload, or None (no layer)."""
    if not path:
        print(f"{name}: no file given -> layer skipped")
        return None
    if not os.path.exists(path):
        print(f"{name}: file not found -> layer skipped: {path}")
        return None
    try:
        from netCDF4 import Dataset
    except ImportError:
        print(f"{name}: netCDF4 is not installed in this environment -> layer skipped")
        return None
    var, cnt = acfg.get("variable", "aod550"), acfg.get("count_variable", "count")
    vmax = float(acfg.get("max_value", 5.0))
    miss = acfg.get("missing_at_or_below", 0.0)          # <= this = missing (exact zeros); null = keep all
    f = max(1, int(acfg.get("coarsen", 1)))              # f x f cells -> 1 (count-weighted), 1 = native grid
    with Dataset(path) as nc:
        lon, lat = np.asarray(nc["lon"][:], float), np.asarray(nc["lat"][:], float)
        lon = np.where(lon > 180, lon - 360, lon)                   # 0..360 -> -180..180
        xo, yo = np.argsort(lon), np.argsort(lat)[::-1]             # columns west->east, rows north->south
        nx2, ny2 = lon.size // f * f, lat.size // f * f             # trim to whole blocks
        xo, yo = xo[:nx2], yo[:ny2]
        lon, lat = lon[xo].reshape(-1, f).mean(1), lat[yo].reshape(-1, f).mean(1)
        dlon, dlat = float(np.median(np.diff(lon))), float(-np.median(np.diff(lat)))
        if not (np.allclose(np.diff(lon), dlon, atol=1e-3 * dlon) and np.allclose(-np.diff(lat), dlat, atol=1e-3 * dlat)):
            print(f"  WARNING: {name} grid is not regular; it will be drawn as if it were")
        flip = nc[var].dimensions[-2:] == ("lon", "lat")            # (time, lon, lat) files
        frames, sum_w, sum_wa = [], 0.0, 0.0
        for k, t in enumerate(nc_times(nc["time"])):
            if not (WIN_START <= t <= WIN_END):                     # both ends: same span as the charts
                continue
            slot = int(round((t - WIN_START) / pd.Timedelta(hours=1)))
            if any(fr["h"] == slot for fr in frames):
                continue
            a = np.ma.filled(nc[var][k], np.nan).astype(np.float64)
            w = np.ma.filled(nc[cnt][k], 0).astype(np.float64) if cnt in nc.variables else np.ones(a.shape)
            if flip:
                a, w = a.T, w.T
            a, w = a[np.ix_(yo, xo)], w[np.ix_(yo, xo)]
            ok = np.isfinite(a) & (w > 0)
            if miss is not None:
                ok &= a > miss                                      # e.g. exact zeros -> missing (and not in the mean)
            w = np.where(ok, w, 0.0)
            wa = w * np.where(ok, a, 0.0)
            if f > 1:
                w, wa = block_sum(w, f), block_sum(wa, f)
            if not (w > 0).any():
                continue                                            # hour without data: not selectable
            sum_w, sum_wa = sum_w + w, sum_wa + wa
            with np.errstate(invalid="ignore", divide="ignore"):
                aod = np.where(w > 0, wa / w, np.nan)
            frames.append({"h": slot, "n": int((w > 0).sum()), "png": png_b64(aod, vmax)})
    if not frames:
        print(f"{name}: no data inside the window -> layer skipped")
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(sum_w > 0, sum_wa / sum_w, np.nan)
    frames.sort(key=lambda fr: fr["h"])
    frames.insert(0, {"mean": True, "n": int(np.isfinite(mean).sum()), "png": png_b64(mean, vmax)})
    size_kb = sum(len(fr["png"]) for fr in frames) / 1024
    print(f"{name}: {len(frames) - 1} hours with data + mean, grid {lon.size} x {lat.size}, {size_kb:.0f} KB embedded")
    return {"grid": {"west": float(lon[0] - dlon / 2), "east": float(lon[-1] + dlon / 2),
                     "north": float(lat[0] + dlat / 2), "south": float(lat[-1] - dlat / 2),
                     "nx": int(lon.size), "ny": int(lat.size)},
            "vmax": vmax, "frames": frames}


def aod_scale(acfg, section):
    """Colour scale of an AOD layer: its own bounds/colors, else a color_scales entry
    (default "AOD 550 nm", the same colours as the AERONET circles)."""
    if "bounds" in acfg:
        b = [float(x) for x in acfg["bounds"]]
        c = acfg.get("colors") or default_colors[:len(b)]
        if len(c) != len(b):
            sys.exit(f"reference.{section}: need one colour per bound")
        return {"bounds": b, "colors": list(c)}
    s = color_scales.get(acfg.get("color_scale", "AOD 550 nm"))
    if not s:
        sys.exit(f"reference.{section}: no colour scale (give bounds/colors or a color_scales entry)")
    return s


AOD_LAYERS = {   # page key -> (config section, default label, file from the command line)
    "viirs": ("viirs_aod", "VIIRS AOD 550 nm", ARGS.viirs_file),
    "tempo": ("tempo_aod", "TEMPO AOD 550 nm", ARGS.tempo_file),
}
aod_data, aod_page = {}, {}
for key, (section, label, path) in AOD_LAYERS.items():
    acfg = REF_CFG.get(section) or {}
    label = acfg.get("label", label)
    aod_data[key] = load_aod(path, acfg, label)
    aod_page[key] = {"label": label, "show": bool(acfg.get("show", False)),
                     "decimals": int(acfg.get("decimals", 2)), "scale": aod_scale(acfg, section)}
ad = REF_CFG.get("aod_display") or {}
# AOD figure (PNG button): regions [west, east, south, north] and boundary lines (GeoJSON,
# fetched by the browser only when a figure is saved)
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
sn = REF_CFG.get("aod_snapshot") or {}
DEFAULT_REGIONS = {"CONUS": {"bounds": [-130, -55, 23, 51], "lambert_bounds": [-126, -69, 23, 50],
                             "lambert_origin": [38.5, -97.5]},
                   "North America": {"bounds": [-180, -40, 12, 73], "lambert_bounds": [-160, -55, 15, 68],
                                     "lambert_origin": [41.75, -105]}}
regions = []
for n, r in (sn.get("regions") or DEFAULT_REGIONS).items():
    r = r if isinstance(r, dict) else {"bounds": r}          # "name: [w, e, s, n]" or "name: {bounds, lambert_origin}"
    b = [float(x) for x in r["bounds"]]
    lb = [float(x) for x in (r.get("lambert_bounds") or b)]     # box used with Lambert (default: bounds)
    if len(b) != 4 or len(lb) != 4:
        sys.exit(f"reference.aod_snapshot.regions: '{n}' needs bounds/lambert_bounds [west, east, south, north]")
    # Lambert conformal: origin [lat, lon] (tangent cone at lat); default = centre of the Lambert box
    o = [float(x) for x in (r.get("lambert_origin") or [(lb[2] + lb[3]) / 2, (lb[0] + lb[1]) / 2])]
    regions.append({"name": str(n), "bounds": b, "lambertBounds": lb, "origin": o})
proj = str(sn.get("projection", "mercator")).lower()
if proj not in ("mercator", "latlon", "lambert"):
    sys.exit("reference.aod_snapshot.projection: mercator | latlon | lambert")
aod_snapshot = {
    "width": int(sn.get("width", 1200)),
    "projection": proj,                     # preselected in the save window
    "regions": regions,
    "boundaries": sn.get("boundaries") or [
        # outlines of polygons with the large lakes cut out; drawn in this order (countries on top)
        {"url": NE + "ne_50m_admin_1_states_provinces_lakes.geojson", "color": "#707070", "width": 0.5},
        {"url": NE + "ne_50m_admin_0_countries_lakes.geojson", "color": "#3a3a3a", "width": 0.8}],
}
aod_display = {"start": str(ad.get("start", "mean")), "opacity": float(ad.get("opacity", 0.75)),
               "frame_ms": int(ad.get("frame_ms", 700))}

# =========================================================================
# 4. COLLECT ALL PLATFORMS AND WRITE THE HTML
# =========================================================================
all_data, counts = {}, []
for plat in PLATFORMS:
    all_data[plat] = platform_payload(platforms_db[plat])
    counts.append(f"{plat}: {len(all_data[plat]['stations'])} stations")
counts.append(f"HMS fires: {len(fires['lon'])}")
for key, d in aod_data.items():
    counts.append(f"{aod_page[key]['label']}: {len(d['frames']) - 1 if d else 0} h")
ref_data = {"hms": {"fires": fires, "series": fire_series}, **aod_data}

# Models panel: built from the model runs found in the CSVs. config.yaml 'models' sets the
# order and colour of each model and the line styles (newest run first); models not listed
# there are added at the end with fallback colours. Public names from model_display_names.
mcfg = cfg["models"]
display_names = cfg.get("model_display_names") or {}
styles = mcfg.get("line_styles") or ["solid", "dash", "dot", "dashdot"]
fallback = mcfg.get("fallback_colors") or ["#17becf", "#bcbd22", "#e377c2", "#7f7f7f", "#ff7f0e"]
known = list(mcfg.get("groups") or {})
found = sorted({r["model"] for r in model_runs.values()})
obs = mcfg.get("observation") or {}
models = [{"group": "Observation", "label": obs.get("label", "Observation"), "items": [{
    "id": "Observation", "label": obs.get("label", "Observation"), "color": obs.get("color", "#000000"),
    "style": "solid", "obs": True, "every": 1}]}]
extra = [g for g in found if g not in known]
for k, g in enumerate([g for g in known if g in found] + extra):
    gcfg = (mcfg["groups"].get(g) or {}) if g in known else {}
    color = gcfg.get("color") or fallback[k % len(fallback)]
    every = gcfg.get("output_hours", mcfg.get("default_output_hours", 1))   # hours between model outputs
    runs = sorted((c for c, r in model_runs.items() if r["model"] == g),
                  key=lambda c: model_runs[c]["init"], reverse=True)       # newest first
    models.append({"group": g, "label": display_names.get(g, g), "color": color, "items": [{
        "id": c, "label": model_runs[c]["init"].strftime("%Y-%m-%d %HZ"), "color": color, "every": every,
        "style": styles[j % len(styles)]} for j, c in enumerate(runs)]})

# Settings the page needs (everything else stays in Python)
page_cfg = {
    "platforms": PLATFORMS,
    "variables": cfg["variables"],          # {variable: unit}, button order
    "models": models,
    "map": cfg["map"],
    "stations": cfg["stations"],
    "hms": cfg["hms"],
    "colorScales": color_scales,
    # Variables with at least one observation this cycle, per platform (others greyed out)
    "available": {p: [v for v in pc["variables"]
                      if any(st["obs_max"].get(v) is not None for st in all_data[p]["stations"].values())]
                  for p, pc in PLATFORMS.items()},
    # Reference layers (left panel + layer control)
    "ref": {
        "hms": {"label": (REF_CFG.get("hms_fires") or {}).get("label", "HMS fires"),
                "show": bool((REF_CFG.get("hms_fires") or {}).get("show", False))},
        **aod_page,                                 # viirs, tempo: label, show, decimals, scale
        "aodDisplay": aod_display,                  # shared: start frame, opacity, animation speed
        "aodSnapshot": aod_snapshot,                # PNG figure: regions + boundary lines
    },
    "autoCollapse": mcfg.get("auto_collapse_seconds", 1.5),
    "startPlatform": None if cfg.get("start_platform") is None else str(cfg["start_platform"]),
    "decimals": {v: decimals_for(v) for v in [*cfg["variables"], "FRP"]},
    "cycle": CYCLE.strftime("%Y-%m-%d %H:%M UTC"),
    "cycleDate": CYCLE.strftime("%Y-%m-%d"),
    "window": [WIN_START.strftime("%Y-%m-%d %H:%M"), WIN_END.strftime("%Y-%m-%d %H:%M")],   # x-axis range
    "dateNav": cfg.get("date_navigation") or None,
}
pop = cfg["popup"]

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Air Quality Model Verification System</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
    <script src="__PLOTLY__"></script>
    <style>
        /* Colours for the two themes (dark = default; body.light switches) */
        :root { --bg: __DARK_BG__; --fg: #ededed; --title: #f5f5f5; --rule: #2e2e2e; --border: #2a2a2a;
                --btn-bg: #262626; --btn-fg: #e5e5e5; --btn-border: #404040; --btn-hover: #333333;
                --hdr-fg: #f1f5f9; --row-fg: #d4d4d4; --map-bg: #111111;
                --legend-bg: rgba(23,23,23,0.88); --legend-fg: #ededed; --legend-hover: #333333; --muted: #9a9a9a; }
        body.light { --bg: __LIGHT_BG__; --fg: #1f2937; --title: #111827; --rule: #cfcfcf; --border: #d0d0d0;
                --btn-bg: #ffffff; --btn-fg: #1f2937; --btn-border: #c4c4c4; --btn-hover: #f3f4f6;
                --hdr-fg: #111827; --row-fg: #374151; --map-bg: #dddddd;
                --legend-bg: rgba(255,255,255,0.93); --legend-fg: #1f2937; --legend-hover: #e5e7eb; --muted: #6b7280; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
        html, body { height: 100%; width: 100%; overflow: hidden; background: var(--bg); }
        #app-container { display: flex; height: 100vh; height: 100dvh; width: 100vw; position: relative; --panel-w: 360px; }
        /* Left panel: platform + variables stay put, the models list scrolls on its own */
        #left-panel { width: var(--panel-w); flex: none; height: 100%; background: var(--bg); color: var(--fg); z-index: 1000;
                      border-right: 1px solid var(--border); padding: 12px 16px 0; display: flex; flex-direction: column; gap: 18px;
                      overflow: hidden; transition: width 0.25s ease, padding 0.25s ease; }
        #left-panel > * { min-width: calc(var(--panel-w) - 32px); }      /* contents keep their width while it folds */
        #left-panel.collapsed { width: 0; padding-left: 0; padding-right: 0; border-right: none; }
        /* Show/hide tab on the right side of the panel (top) */
        #panel-toggle { position: absolute; top: 12px; left: var(--panel-w); z-index: 1001; width: 24px; height: 46px;
                        border: 1px solid var(--border); border-left: none; border-radius: 0 7px 7px 0; background: var(--bg);
                        color: var(--fg); font-size: 15px; font-weight: 700; cursor: pointer; transition: left 0.25s ease;
                        box-shadow: 2px 0 6px rgba(0,0,0,0.3); }
        #panel-toggle:hover { background: var(--btn-hover); }
        #app-container.panel-hidden #panel-toggle { left: 0; }
        .leaflet-top.leaflet-left .leaflet-control-zoom { margin-left: 36px; }   /* clear of the tab */
        #panel-top { display: flex; justify-content: flex-end; margin-bottom: -8px; }
        @media (max-width: 700px) {                      /* phones: the panel slides over the map */
            #app-container { --panel-w: 300px; }
            #left-panel { position: absolute; left: 0; top: 0; box-shadow: 4px 0 16px rgba(0,0,0,0.5); }
            .leaflet-top.leaflet-left .leaflet-control-zoom { margin-left: 10px; margin-top: 66px; }
            #aod-slider { gap: 6px; padding: 6px 8px; font-size: 12px; }
            #aod-time-lbl { width: 128px; }
            .ts-btn { padding: 3px 7px; min-width: 30px; font-size: 13px; }
        }
        @media (max-height: 760px) {                     /* short screens: the whole panel scrolls */
            #left-panel { overflow-y: auto; }
            #models-section { flex: none; }
            #models-list { overflow: visible; }
            #left-panel.collapsed { overflow: hidden; }
        }
        #models-section { flex: 1; min-height: 0; display: flex; flex-direction: column; }
        #models-list { flex: 1; min-height: 0; overflow-y: auto; padding: 2px 6px 16px 0; }
        #map-container { flex: 1; height: 100%; position: relative; }
        #map { height: 100%; width: 100%; background: var(--map-bg); }
        .panel-title { font-size: 14px; font-weight: 700; color: var(--title); margin-bottom: 10px; display: flex; align-items: center;
                       border-bottom: 2px solid var(--rule); padding-bottom: 6px; text-transform: uppercase; letter-spacing: 0.6px; }
        #theme-btn { font-size: 11px; font-weight: 600; text-transform: none; letter-spacing: 0;
                     padding: 3px 9px; border-radius: 12px; border: 1px solid var(--btn-border); background: var(--btn-bg);
                     color: var(--btn-fg); cursor: pointer; }
        #theme-btn:hover { background: var(--btn-hover); }
        /* Cycle date picker */
        #date-section { position: relative; }
        #date-btn { margin-left: auto; font-size: 13px; font-weight: 600; text-transform: none; letter-spacing: 0;
                    padding: 4px 10px; border-radius: 6px; border: 1px solid var(--btn-border); background: var(--btn-bg);
                    color: var(--btn-fg); cursor: pointer; }
        #date-btn:hover { background: var(--btn-hover); }
        #date-pop { position: absolute; right: 0; top: 34px; z-index: 2000; width: 252px; padding: 10px;
                    background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-border);
                    border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.45); }
        #date-pop[hidden] { display: none; }
        .cal-head { display: flex; align-items: center; justify-content: space-between; font-weight: 700; font-size: 13px; margin-bottom: 6px; }
        .cal-head button { background: none; border: none; color: var(--btn-fg); font-size: 16px; cursor: pointer; padding: 0 6px; }
        .cal-head button:disabled { opacity: 0.25; cursor: default; }
        .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; text-align: center; font-size: 12px; }
        .cal-grid .dow { color: var(--muted); font-size: 10.5px; padding: 2px 0; }
        .cal-day { padding: 5px 0; border-radius: 4px; cursor: pointer; border: 1px solid transparent; }
        .cal-day:hover { background: var(--btn-hover); }
        .cal-day.off { opacity: 0.25; cursor: default; background: none; }
        .cal-day.checking { opacity: 0.6; }
        .cal-day.current { background: #0284c7; color: #fff; }
        .cal-day.today { border-color: #38bdf8; }
        .cal-note { color: var(--muted); font-size: 10.5px; margin-top: 6px; }
        input[type="checkbox"] { accent-color: #0284c7; width: 15px; height: 15px; cursor: pointer; }
        .model-group { margin-bottom: 12px; }
        .model-header { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 700;
                        color: var(--hdr-fg); margin-bottom: 4px; user-select: none; }
        .hdr-left { display: flex; align-items: center; gap: 8px; flex: 1; cursor: pointer; }
        .caret, .caret-spacer { width: 20px; flex: none; }
        .caret { background: none; border: none; color: var(--fg); font-size: 18px; line-height: 1; cursor: pointer; padding: 0;
                 transition: transform 0.15s ease; }
        .model-group.collapsed .caret { transform: rotate(-90deg); }
        .model-items { overflow: hidden; transition: max-height 0.45s ease, opacity 0.45s ease; }
        .model-group.collapsed .model-items { max-height: 0 !important; opacity: 0; }
        .trace-row { display: flex; align-items: center; justify-content: space-between; font-size: 13px;
                     padding: 4px 0 4px 45px; color: var(--row-fg); }
        .no-data { opacity: 0.4; }
        .no-data input { cursor: not-allowed; }
        .trace-left { display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; }
        .trace-controls { display: flex; align-items: center; gap: 6px; }
        input[type="color"] { border: 1px solid var(--btn-border); width: 24px; height: 22px; border-radius: 4px; cursor: pointer; padding: 0; background: none; }
        select.style-select { font-size: 12px; padding: 3px 6px; border: 1px solid var(--btn-border); border-radius: 4px;
                              background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
        .btn-toggle { padding: 10px 5px; font-size: 13px; font-weight: 600; text-align: center; background: var(--btn-bg); color: var(--btn-fg);
                      border: 1px solid var(--btn-border); border-radius: 6px; cursor: pointer; transition: all 0.15s ease; user-select: none;
                      display: flex; align-items: center; justify-content: center; line-height: 1.2; }
        .btn-toggle:hover { background: var(--btn-hover); }
        .btn-toggle.disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
        .btn-toggle.active { background: #0284c7; color: #ffffff; border-color: #38bdf8; box-shadow: 0 0 8px rgba(2, 132, 199, 0.4); }
        /* Popups: chart box of fixed size so Leaflet can place the popup before Plotly draws */
        .leaflet-popup-content-wrapper { padding: 0; border-radius: 8px; overflow: hidden; box-shadow: 0 6px 24px rgba(0,0,0,0.45); }
        .leaflet-popup-content { margin: 0; line-height: normal; }
        .popup-below .leaflet-popup-tip-container { top: -20px; transform: rotate(180deg); }   /* tip points up */
        .plot-box { width: __POP_W__; height: __POP_H__; min-width: __POP_MINW__; min-height: __POP_MINH__; background: #fff; }
        .plot-box .modebar { right: 34px !important; }                     /* clear the close (x) button */
        /* Station charts: legend in its own panel, right of the chart (below it on narrow windows) */
        .plot-wrap { display: flex; background: #fff; }
        .plot-wrap .plot-box .modebar { right: 6px !important; }           /* the (x) is over the legend */
        .plot-legend { flex: none; width: 200px; height: __POP_H__; min-height: __POP_MINH__; overflow-y: auto;
                       padding: 38px 8px 8px 10px; border-left: 1px solid #e2e2e2; background: #fafafa;
                       font-size: 11.5px; color: #1f2937; user-select: none; }
        .plot-legend .lg-grp { font-weight: 700; margin: 6px 0 1px; cursor: pointer; border-radius: 3px; padding: 0 3px; }
        .plot-legend .lg-row { display: flex; align-items: center; gap: 6px; cursor: pointer; border-radius: 3px;
                               padding: 1px 3px; white-space: nowrap; }
        .plot-legend .lg-row svg { flex: none; }
        .plot-legend .lg-grp:hover, .plot-legend .lg-row:hover { background: #e5e7eb; }
        .plot-legend .off { opacity: 0.35; text-decoration: line-through; }
        .plot-legend .hint { color: #6b7280; margin-top: 6px; white-space: normal; }
        @media (max-width: 1100px) {
            .plot-wrap { flex-direction: column; }
            .plot-wrap .plot-box .modebar { right: 34px !important; }
            .plot-legend { width: auto; height: 110px; min-height: 0; padding: 6px 8px; border-left: none;
                           border-top: 1px solid #e2e2e2; display: flex; flex-wrap: wrap; align-content: flex-start;
                           column-gap: 12px; }
            .plot-legend .lg-grp { width: 100%; margin: 3px 0 0; }
            .plot-legend .hint { width: 100%; }
        }
        .plot-box .modebar-group { background: transparent !important; }
        .leaflet-container a.leaflet-popup-close-button {                   /* always visible, above the chart */
            z-index: 1100; width: 26px; height: 26px; top: 6px; right: 6px; font: 22px/24px Tahoma, Verdana, sans-serif;
            color: #444; background: rgba(255,255,255,0.9); border-radius: 50%; text-align: center; }
        .leaflet-container a.leaflet-popup-close-button:hover { color: #000; background: #e5e5e5; }
        .plot-msg { display: flex; flex-direction: column; align-items: center; justify-content: center;
                    height: 100%; color: #555; text-align: center; padding: 24px; }
        .plot-msg h3 { margin-bottom: 8px; color: #111; font-size: 16px; }
        #status { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 1000;
                  background: var(--legend-bg); color: var(--legend-fg); padding: 8px 14px; border-radius: 6px;
                  font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); display: none; }
        /* Legends */
        .fire-legend, .color-legend { background: var(--legend-bg); color: var(--legend-fg); padding: 8px 10px; border-radius: 6px;
                       font-size: 12px; line-height: 1.5; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        .fire-legend b, .color-legend b { display: block; margin-bottom: 3px; }
        .fire-legend .row, .color-legend .row { display: flex; align-items: center; gap: 7px; }
        .fire-legend svg { flex: none; }
        .color-legend .sw { width: 12px; height: 12px; border-radius: 50%; border: 1px solid #111; flex: none; }
        .sat-row, .bin-row { cursor: pointer; border-radius: 3px; padding: 0 4px; margin: 0 -4px; user-select: none; }
        .sat-row:hover, .bin-row:hover { background: var(--legend-hover); }
        .sat-row.off, .bin-row.off { opacity: 0.35; text-decoration: line-through; }
        .cnt { color: var(--muted); margin-left: auto; padding-left: 10px; }
        .hint { color: var(--muted); font-size: 10.5px; margin-top: 2px; }
        .show-all { color: #0284c7; cursor: pointer; font-size: 11px; }
        .fire-card { padding: 12px 40px 12px 14px; font-size: 13px; color: #0f172a; min-width: 230px; }
        .fire-card h4 { font-size: 14px; margin-bottom: 8px; }
        .fire-card table { border-collapse: collapse; }
        .fire-card td { padding: 2px 10px 2px 0; }
        .fire-card td:first-child { color: #555; }
        /* Reference observations (left panel) */
        .ref-row { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; padding: 3px 0;
                   color: var(--hdr-fg); cursor: pointer; user-select: none; }
        .ref-row .cnt { font-weight: 400; }
        .ref-row.no-data { opacity: 0.4; cursor: not-allowed; }
        .ref-sub { display: flex; align-items: center; gap: 6px; padding: 3px 0 2px 23px; font-size: 12px; color: var(--row-fg); }
        .ref-sub.off { opacity: 0.4; pointer-events: none; }
        .ref-sub select { flex: 1; min-width: 0; }
        .ref-sub .step { font-size: 11px; padding: 3px 7px; border: 1px solid var(--btn-border); border-radius: 4px;
                         background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; }
        .ref-sub .step:hover { background: var(--btn-hover); }
        .ref-sub input[type="range"] { flex: 1; accent-color: #0284c7; }
        /* VIIRS AOD image: hard cell edges instead of blurred upscaling */
        .aod-img { image-rendering: pixelated; image-rendering: crisp-edges; }
        #aod-hover { position: absolute; z-index: 1001; pointer-events: none; display: none; white-space: nowrap;
                     background: var(--legend-bg); color: var(--legend-fg); font-size: 12px; padding: 3px 7px;
                     border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,0.4); }
        .aod-legend { background: var(--legend-bg); color: var(--legend-fg); padding: 8px 12px 4px; border-radius: 6px;
                      font-size: 12px; line-height: 1.4; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        .aod-legend b { display: block; }
        .aod-legend .sub { color: var(--muted); font-size: 11px; margin-bottom: 5px; }
        .cbar { display: flex; height: 11px; border: 1px solid #111; }
        .cbar span { flex: 1; }
        .cbar-ticks { position: relative; height: 16px; font-size: 10.5px; }
        .cbar-ticks span { position: absolute; top: 1px; transform: translateX(-50%); }
        /* AOD time slider: centre bottom of the map */
        #aod-slider { position: absolute; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 1000;
                      width: min(816px, calc(100% - 32px)); display: flex; align-items: center; gap: 10px;
                      background: var(--legend-bg); color: var(--legend-fg); padding: 8px 14px; border-radius: 10px;
                      font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
        #aod-slider[hidden] { display: none; }
        .ts-btn { font-size: 14px; font-weight: 600; padding: 4px 11px; min-width: 38px; border-radius: 6px; cursor: pointer;
                  border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--btn-fg); }
        .ts-btn:hover { background: var(--btn-hover); }
        .ts-btn.active { background: #0284c7; color: #fff; border-color: #38bdf8; }
        .ts-btn:disabled { opacity: 0.4; cursor: default; }
        .ts-track { position: relative; flex: 1; min-width: 80px; height: 30px; }
        .ts-ticks { position: absolute; left: 8.5px; right: 8.5px; top: 21px; height: 7px; pointer-events: none; }
        .ts-ticks span { position: absolute; width: 2px; height: 7px; margin-left: -1px; background: #38bdf8; border-radius: 1px; }
        #aod-range { -webkit-appearance: none; appearance: none; position: relative; width: 100%; height: 22px;
                     margin: 0; background: transparent; cursor: pointer; }
        #aod-range::-webkit-slider-runnable-track { height: 5px; border-radius: 3px; background: var(--btn-border); }
        #aod-range::-moz-range-track { height: 5px; border-radius: 3px; background: var(--btn-border); }
        #aod-range::-webkit-slider-thumb { -webkit-appearance: none; width: 17px; height: 17px; margin-top: -6px;
                     border-radius: 50%; background: #0284c7; border: 2px solid #fff; }
        #aod-range::-moz-range-thumb { width: 17px; height: 17px; box-sizing: border-box; border-radius: 50%;
                     background: #0284c7; border: 2px solid #fff; }
        #aod-slider.mean #aod-range::-webkit-slider-thumb { background: #888; }
        #aod-slider.mean #aod-range::-moz-range-thumb { background: #888; }
        /* AOD figure options (centre of the window) */
        #snap-modal { position: fixed; inset: 0; z-index: 3000; background: rgba(0,0,0,0.45);
                      display: flex; align-items: center; justify-content: center; }
        #snap-modal[hidden] { display: none; }
        .snap-box { width: min(380px, 92vw); background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-border);
                    border-radius: 10px; padding: 18px 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); font-size: 13px; }
        .snap-box h3 { font-size: 15px; margin-bottom: 4px; }
        .snap-box .sub { color: var(--muted); font-size: 12px; margin-bottom: 12px; }
        .snap-box label { display: flex; align-items: center; gap: 8px; padding: 5px 0; cursor: pointer; }
        .snap-box label .cnt { font-size: 11px; }
        .snap-box .grp { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         color: var(--muted); margin: 8px 0 2px; }
        .snap-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
        .snap-btns button { font-size: 13px; padding: 6px 14px; border-radius: 6px; cursor: pointer;
                            border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--btn-fg); }
        .snap-btns button.primary { background: #0284c7; color: #fff; border-color: #38bdf8; }
        .snap-btns button:disabled { opacity: 0.5; cursor: default; }
        #aod-time-lbl { flex: none; width: 168px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
    </style>
</head>
<body>
    <div id="app-container">
        <div id="left-panel">
            <div id="panel-top"><button id="theme-btn" type="button"></button></div>
            <div id="date-section"><div class="panel-title">Cycle date<button id="date-btn" type="button"></button></div>
                 <div id="date-pop" hidden></div></div>
            <div id="ref-section"><div class="panel-title">Reference observations</div>
                 <label class="ref-row"><input type="checkbox" id="ref_hms"><span id="ref_hms_lbl"></span><span class="cnt" id="ref_hms_cnt"></span></label>
                 <label class="ref-row"><input type="checkbox" id="ref_viirs"><span id="ref_viirs_lbl"></span><span class="cnt" id="ref_viirs_cnt"></span></label>
                 <label class="ref-row"><input type="checkbox" id="ref_tempo"><span id="ref_tempo_lbl"></span><span class="cnt" id="ref_tempo_cnt"></span></label>
                 <div class="ref-sub" id="aod-opac"><span>Opacity</span><input type="range" id="aod-opacity" min="0.1" max="1" step="0.05">
                     <button class="step" type="button" id="aod-save" title="Save the AOD as a PNG figure (white background, boundaries only)">⤓ PNG</button></div></div>
            <div><div class="panel-title">Observation Platform</div>
                 <div class="grid-3" id="platform-container"></div></div>
            <div><div class="panel-title">Variable</div><div class="grid-3" id="variable-container"></div></div>
            <div id="models-section"><div class="panel-title">Models & Initializations</div><div id="models-list"></div></div>
        </div>
        <button id="panel-toggle" type="button" title="Hide the panel">«</button>
        <div id="map-container"><div id="map"></div><div id="status"></div><div id="aod-hover"></div>
            <!-- AOD time slider (shown while VIIRS or TEMPO is on) -->
            <div id="aod-slider" hidden>
                <button class="ts-btn" type="button" id="aod-mean" title="Mean of the hours with data">Mean</button>
                <button class="ts-btn" type="button" id="aod-play">▶</button>
                <div class="ts-track"><div class="ts-ticks" id="aod-ticks"></div>
                     <input type="range" id="aod-range" min="0" step="1" value="0" title="Hours with data are marked; others are skipped"></div>
                <span id="aod-time-lbl"></span>
            </div></div>
    </div>
    <!-- AOD figure options -->
    <div id="snap-modal" hidden><div class="snap-box">
        <h3>Save AOD figure (PNG)</h3>
        <div class="sub" id="snap-sub"></div>
        <div id="snap-opts"></div>
        <div class="snap-btns"><button type="button" id="snap-cancel">Cancel</button>
             <button type="button" class="primary" id="snap-go">Save PNG</button></div>
    </div></div>

    <script>
        // ---------------- written by generate_dashboard.py ----------------
        const CFG = __CFG__;              // settings from config.yaml
        const DATA = __DATA__;            // {platform: {species, stations}}
        const REF = __REF__;              // {hms: {fires, series}, viirs|tempo: {grid, vmax, frames} | null}

        const PLATFORMS = CFG.platforms, UNITS = CFG.variables, COLOR_SCALES = CFG.colorScales;
        const VARIABLES = Object.keys(UNITS), PLATFORM_NAMES = Object.keys(PLATFORMS);
        const SAT_STYLE = CFG.hms.satellites, SAT_OTHER = CFG.hms.other_satellite;
        const NO_DATA_COLOR = CFG.stations.no_data_color;
        const AVAIL = CFG.available;      // platform -> variables with observations this cycle
        const firstVar = plat => AVAIL[plat].includes(PLATFORMS[plat].default) ? PLATFORMS[plat].default
                                 : (AVAIL[plat][0] || PLATFORMS[plat].default);
        // Starting platform: config start_platform (a name, or none), else the first one with data
        const START = CFG.startPlatform;
        let activePlatform = START === "none" ? null
                           : (START && AVAIL[START] && AVAIL[START].length ? START
                              : PLATFORM_NAMES.find(p => AVAIL[p].length) || PLATFORM_NAMES[0]);
        let activeVariable = activePlatform ? firstVar(activePlatform) : null;

        const varBtnId = v => "btn_var_" + v.replace(/[^a-zA-Z0-9]/g, "_");
        const decOf = v => CFG.decimals[v] ?? 2;                  // decimal places per variable
        const fmtVal = (x, v) => x.toFixed(decOf(v));             // value with the variable's decimals
        const unitOf = v => (UNITS[v] && UNITS[v] !== "unitless" ? " " + UNITS[v] : "");
        const deg = (v, pos, neg, nd = 4) => `${Math.abs(v).toFixed(nd)}°${v >= 0 ? pos : neg}`;
        const stationsOf = plat => (DATA[plat] && DATA[plat].stations) || {};
        const statusBox = document.getElementById("status");
        const showStatus = txt => { statusBox.textContent = txt; statusBox.style.display = txt ? "block" : "none"; };

        // ======================= MODELS (below the variables, scrolls) =======================
        // Observation: one row (checkbox, colour, line style). Each model: a header with a
        // collapse arrow, a checkbox for all its runs (dash = some on) and a colour that sets
        // all its runs at once; below it one row per run. Runs with no data this cycle are
        // unchecked and disabled.
        const modelsList = document.getElementById("models-list");
        const styleSelect = (id, style) => `<select class="style-select" id="sty_${id}">` +
            [["solid", "───"], ["dash", "—"], ["dot", "···"], ["dashdot", "-·-"]]
                .map(([v, t]) => `<option value="${v}" ${style === v ? "selected" : ""}>${t}</option>`).join("") + `</select>`;
        CFG.models.forEach((group, gi) => {
            const groupDiv = document.createElement("div");
            groupDiv.className = "model-group";
            if (group.items.length === 1 && group.items[0].obs) {          // Observation: single row
                const it = group.items[0];
                groupDiv.innerHTML = `<div class="model-header"><span class="caret-spacer"></span>
                    <label class="hdr-left"><input type="checkbox" id="chk_${it.id}" checked><span>${group.label}</span></label>
                    <div class="trace-controls">${styleSelect(it.id, it.style)}<input type="color" id="col_${it.id}" value="${it.color}"></div></div>`;
                modelsList.appendChild(groupDiv);
                groupDiv.querySelector(`#chk_${it.id}`).addEventListener("change", refreshCurrentPlot);
                groupDiv.querySelector(`#col_${it.id}`).addEventListener("input", refreshCurrentPlot);
                groupDiv.querySelector(`#sty_${it.id}`).addEventListener("change", refreshCurrentPlot);
                return;
            }
            groupDiv.innerHTML = `<div class="model-header" id="hdr_${gi}">
                    <button class="caret" type="button" title="Show/hide initializations">▾</button>
                    <label class="hdr-left"><input type="checkbox" id="grp_${gi}" checked><span>${group.label}</span></label>
                    <div class="trace-controls"><input type="color" id="gcol_${gi}" value="${group.color}" title="Colour of all ${group.label} runs"></div>
                </div><div class="model-items"></div>`;
            const items = groupDiv.querySelector(".model-items");
            group.items.forEach(item => {
                const row = document.createElement("div");
                row.className = "trace-row";
                row.id = `row_${item.id}`;
                row.innerHTML = `
                    <label class="trace-left">
                        <input type="checkbox" id="chk_${item.id}" data-group="${gi}" checked>
                        <span>${item.label}</span>
                    </label>
                    <div class="trace-controls">${styleSelect(item.id, item.style)}<input type="color" id="col_${item.id}" value="${item.color}"></div>`;
                items.appendChild(row);
                row.querySelector(`#chk_${item.id}`).addEventListener("change", e => {
                    userOn[item.id] = e.target.checked;             // remembered across platforms/variables
                    syncGroupBox(gi); refreshCurrentPlot();
                });
                row.querySelector(`#col_${item.id}`).addEventListener("input", refreshCurrentPlot);
                row.querySelector(`#sty_${item.id}`).addEventListener("change", refreshCurrentPlot);
            });
            modelsList.appendChild(groupDiv);
            items.style.maxHeight = items.scrollHeight + "px";       // animatable height
            groupDiv.querySelector(".caret").addEventListener("click", () => {
                groupDiv.dataset.touched = "1";                      // user decided; skip auto-collapse
                groupDiv.classList.toggle("collapsed");
            });
            groupDiv.querySelector(`#grp_${gi}`).addEventListener("change", e => {
                group.items.forEach(item => {
                    const b = document.getElementById(`chk_${item.id}`);
                    if (!b.disabled) { b.checked = e.target.checked; userOn[item.id] = e.target.checked; }
                });
                syncGroupBox(gi);
                refreshCurrentPlot();
            });
            groupDiv.querySelector(`#gcol_${gi}`).addEventListener("input", e => {    // model colour -> all its runs
                group.items.forEach(item => document.getElementById(`col_${item.id}`).value = e.target.value);
                refreshCurrentPlot();
            });
        });
        // Model runs without data for the selected platform + variable: greyed out and
        // unchecked; the user's own on/off choice comes back where the run has data.
        const userOn = {};                // run id -> checked by the user (default on)
        function updateModelAvailability() {
            const sp = DATA[activePlatform] && DATA[activePlatform].species[activeVariable];
            const avail = new Set(sp ? sp.avail : []);
            CFG.models.forEach((group, gi) => {
                if (group.items.length === 1 && group.items[0].obs) return;
                let any = false;
                group.items.forEach(item => {
                    const ok = avail.has(item.id), b = document.getElementById(`chk_${item.id}`), row = document.getElementById(`row_${item.id}`);
                    b.disabled = !ok;
                    b.checked = ok && (userOn[item.id] ?? true);
                    row.classList.toggle("no-data", !ok);
                    row.title = ok ? "" : "No data for this platform and variable";
                    any = any || ok;
                });
                const g = document.getElementById(`grp_${gi}`), hdr = document.getElementById(`hdr_${gi}`);
                g.disabled = !any;
                hdr.classList.toggle("no-data", !any);
                hdr.title = any ? "" : "No data for this platform and variable";
                if (any) syncGroupBox(gi); else { g.checked = false; g.indeterminate = false; }
            });
        }
        // Model groups start expanded, then fold up after config models.auto_collapse_seconds
        if (CFG.autoCollapse) setTimeout(() => document.querySelectorAll(".model-group").forEach(g => {
            if (g.querySelector(".model-items") && !g.dataset.touched) g.classList.add("collapsed");
        }), CFG.autoCollapse * 1000);

        function syncGroupBox(gi) {       // all on -> checked, all off -> empty, mixed -> dash
            const boxes = [...document.querySelectorAll(`input[data-group="${gi}"]:not(:disabled)`)];
            if (!boxes.length) return;
            const on = boxes.filter(b => b.checked).length, g = document.getElementById(`grp_${gi}`);
            g.checked = on === boxes.length;
            g.indeterminate = on > 0 && on < boxes.length;
        }
        function traceSettings() {        // current settings of every model run
            const out = [];
            CFG.models.forEach(group => group.items.forEach(item => out.push({
                id: item.id,
                name: item.obs ? item.label : `${group.label} ${item.label}`,
                group: group.label, run: item.label,
                visible: document.getElementById(`chk_${item.id}`).checked,
                color: document.getElementById(`col_${item.id}`).value,
                style: document.getElementById(`sty_${item.id}`).value,
                every: item.every || 1,
                isObs: !!item.obs
            })));
            return out;
        }

        // ======================= PLATFORM & VARIABLE BUTTONS =======================
        const platformContainer = document.getElementById("platform-container");
        PLATFORM_NAMES.forEach(plat => {
            const btn = document.createElement("button");
            btn.className = "btn-toggle" + (plat === activePlatform ? " active" : "") + (AVAIL[plat].length ? "" : " disabled");
            if (!AVAIL[plat].length) btn.title = "No data for this cycle";
            btn.textContent = plat;
            if (AVAIL[plat].length) btn.title = "Click again to hide the stations";
            btn.onclick = () => selectPlatform(plat === activePlatform ? null : plat);   // 2nd click = none
            platformContainer.appendChild(btn);
        });
        // Only the selected platform's variables are shown (order as in config.yaml 'variables')
        const variableContainer = document.getElementById("variable-container");
        function buildVariableButtons(plat) {
            variableContainer.innerHTML = plat ? "" : `<div class="hint" style="grid-column: 1 / -1">No platform selected (reference layers only)</div>`;
            if (!plat) return;
            VARIABLES.filter(v => PLATFORMS[plat].variables.includes(v)).forEach(v => {
                const btn = document.createElement("button");
                const ok = AVAIL[plat].includes(v);
                btn.className = "btn-toggle" + (v === activeVariable ? " active" : "") + (ok ? "" : " disabled");
                btn.title = ok ? "Click again to hide the stations" : "No data for this cycle";
                btn.id = varBtnId(v);
                btn.textContent = v;
                btn.onclick = () => selectVariable(v === activeVariable ? null : v);         // 2nd click = none
                variableContainer.appendChild(btn);
            });
        }

        // plat = null -> no platform: no stations, only the reference layers
        function selectPlatform(plat) {
            activePlatform = plat;
            document.querySelectorAll("#platform-container .btn-toggle")
                .forEach(b => b.classList.toggle("active", b.textContent === plat));
            if (plat && !AVAIL[plat].includes(activeVariable)) activeVariable = firstVar(plat);
            if (!plat) activeVariable = null;
            buildVariableButtons(plat);
            updateModelAvailability();
            clearStations();              // closes the open popup first (no reopen on another platform)
            updateMarkers();
        }
        function setVariable(v) {
            activeVariable = v;
            document.querySelectorAll("#variable-container .btn-toggle")
                .forEach(b => b.classList.toggle("active", b.textContent === v));
        }
        // v = null -> no variable: the platform stays selected but its stations are hidden
        function selectVariable(v) {
            const wasNone = activeVariable === null;
            setVariable(v);
            updateModelAvailability();
            if (!v) return clearStations();
            // circles <-> circles: recolour (open popup stays); circles <-> drops / from none: rebuild
            if (!wasNone && !!COLOR_SCALES[v] === colorMode) recolorStations(); else updateMarkers();
            refreshCurrentPlot();
        }

        // ======================= MAP =======================
        const baseLayers = {};
        CFG.map.base_layers.forEach(b => {
            baseLayers[b.name] = L.tileLayer(b.url, { attribution: b.attribution, maxZoom: b.max_zoom || 19 });
        });
        const bnd = CFG.map.boundaries;
        const boundaries = L.tileLayer(bnd.url, { attribution: bnd.attribution, pane: "overlayPane" });
        // Panning limited to config map.bounds ([[south, west], [north, east]]); the
        // minimum zoom is set so the view can never show anything outside them.
        const mapBounds = CFG.map.bounds ? L.latLngBounds(CFG.map.bounds) : null;
        // Smooth zooming (config map.zoom): fractional zoom levels, smaller +/- and
        // scroll-wheel steps; more wheel pixels per zoom level = slower, finer zooming.
        const ZC = CFG.map.zoom_behavior || {};
        const map = L.map("map", { center: CFG.map.center, zoom: CFG.map.zoom,
                                   maxBounds: mapBounds, maxBoundsViscosity: 1.0,
                                   zoomSnap: ZC.snap ?? 0.25, zoomDelta: ZC.delta ?? 0.5,
                                   wheelPxPerZoomLevel: ZC.wheel_px_per_level ?? 120, wheelDebounceTime: 20 });
        function fitMinZoom() {
            if (!mapBounds) return;
            map.setMinZoom(map.getBoundsZoom(mapBounds, true));
            if (map.getZoom() < map.getMinZoom()) map.setZoom(map.getMinZoom());
            map.panInsideBounds(mapBounds, { animate: false });
        }
        fitMinZoom();
        map.on("resize", fitMinZoom);
        const layerControl = L.control.layers(baseLayers, { [bnd.name]: boundaries }, { position: "topright" }).addTo(map);
        // ---- show/hide the left panel (tab on its right side); the map takes the freed space ----
        (function panelToggle() {
            const app = document.getElementById("app-container"), panel = document.getElementById("left-panel");
            const tab = document.getElementById("panel-toggle");
            tab.onclick = () => {
                const hide = !panel.classList.contains("collapsed");
                panel.classList.toggle("collapsed", hide);
                app.classList.toggle("panel-hidden", hide);
                tab.textContent = hide ? "»" : "«";
                tab.title = hide ? "Show the panel" : "Hide the panel";
            };
            panel.addEventListener("transitionend", e => { if (e.propertyName === "width") map.invalidateSize(); });
        })();

        // ---- dark / light theme: panel colours + base map (config map.themes) ----
        const THEMES = CFG.map.themes;
        const themeBtn = document.getElementById("theme-btn");
        function applyTheme(t) {
            const th = THEMES[t];
            document.body.classList.toggle("light", t === "light");
            Object.values(baseLayers).forEach(l => map.removeLayer(l));
            map.addLayer(baseLayers[th.base] || Object.values(baseLayers)[0]);
            if (th.boundaries) map.addLayer(boundaries); else map.removeLayer(boundaries);
            themeBtn.textContent = t === "light" ? "☾ Dark mode" : "☀ Light mode";
            themeBtn.dataset.theme = t;
            try { localStorage.setItem("verif-theme", t); } catch (e) {}        // remembered per browser
        }
        themeBtn.onclick = () => applyTheme(themeBtn.dataset.theme === "light" ? "dark" : "light");
        let savedTheme = "dark";
        try { savedTheme = localStorage.getItem("verif-theme") || "dark"; } catch (e) {}
        applyTheme(THEMES[savedTheme] ? savedTheme : "dark");
        const markerCluster = L.markerClusterGroup().addTo(map);

        // ---- popups that stay inside the map ----
        // Open above the marker as usual; if that would be cut off at the top (panning limit),
        // open below it instead, and slide sideways near the east/west edges. The tip keeps
        // pointing at the marker. No auto-panning (the panning limit would block it anyway).
        const SmartPopup = L.Popup.extend({
            options: { maxWidth: 2000, autoPan: false },
            _updatePosition() {
                L.Popup.prototype._updatePosition.call(this);
                if (!this._map || !this._container) return;
                const c = this._container, tip = this._tipContainer, pad = 8;
                c.classList.remove("popup-below");
                tip.style.marginLeft = "";
                const mapR = this._map.getContainer().getBoundingClientRect();
                const p = this._map.latLngToContainerPoint(this._latlng);
                let r = c.getBoundingClientRect();
                const top = r.top - mapR.top;
                if (top < pad && mapR.height - p.y > p.y) {          // no room above -> below
                    c.classList.add("popup-below");
                    const newTop = p.y + 22;                         // just under the marker
                    c.style.bottom = (parseFloat(c.style.bottom) - (newTop - top)) + "px";
                }
                r = c.getBoundingClientRect();
                const left = r.left - mapR.left;
                let dx = 0;
                if (left < pad) dx = pad - left;
                else if (left + r.width > mapR.width - pad) dx = (mapR.width - pad) - (left + r.width);
                if (dx) {
                    c.style.left = (parseFloat(c.style.left) + dx) + "px";
                    tip.style.marginLeft = (-20 - dx) + "px";        // tip stays on the marker
                }
            }
        });
        const smartPopup = content => new SmartPopup().setContent(content);

        // ======================= CHART HELPERS =======================
        // Extra toolbar button: download the plotted values as CSV
        function downloadCSV(gd) {
            const meta = gd._csv || {};
            const traces = gd.data.filter(t => t.visible !== "legendonly");
            const times = [...new Set(traces.flatMap(t => t.x))].sort();
            const lookup = traces.map(t => new Map(t.x.map((x, i) => [x, meta.blankY ? null : t.y[i]])));
            const q = s => (/[",]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
            const lines = (meta.header || []).map(h => "# " + h);
            lines.push(["time_utc", ...traces.map(t => q(t.name))].join(","));
            times.forEach(x => {
                const vals = lookup.map(m => { const v = m.get(x); return v === null || v === undefined ? "" : v; });
                if (meta.blankY || vals.some(v => v !== "")) lines.push([x, ...vals].join(","));
            });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
            a.download = (meta.filename || "data") + ".csv";
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        }
        // Save the chart as a PNG of 765 x 495 css px at scale 2 = 1530 x 990 px. Station
        // charts (gd._png set) get their legend at the bottom in columns: the bottom margin
        // grows by the legend height, so the plot itself gets shorter, never the image.
        const PNG_W = 765, PNG_H = 495;
        function savePlotPNG(gd) {
            const data = JSON.parse(JSON.stringify(gd.data.filter(t => t.visible !== "legendonly")));
            const layout = JSON.parse(JSON.stringify(gd.layout));
            const P = gd._png || {};
            Object.assign(layout, P.layout || {}, { width: PNG_W, height: PNG_H, autosize: false });
            if (P.legend && data.length) {
                const longest = Math.max(...data.map(t => (t.name || "").length));
                const entry = Math.min(PNG_W - 40, Math.round(longest * 6 + 48));          // px per legend column
                const cols = Math.max(1, Math.floor((PNG_W - 30) / entry));
                const rows = Math.ceil(data.length / cols), lh = rows * 19 + 10;
                layout.showlegend = true;
                layout.legend = { orientation: "h", xref: "container", yref: "container", x: 0.5, xanchor: "center",
                                  y: 6 / PNG_H, yanchor: "bottom", entrywidth: entry, entrywidthmode: "pixels",
                                  traceorder: "normal", font: { size: 10 } };
                layout.margin = Object.assign({}, layout.margin, { b: 48 + lh });
            }
            Plotly.toImage({ data, layout }, { format: "png", width: PNG_W, height: PNG_H, scale: 2 }).then(url => {
                const a = document.createElement("a");
                a.href = url;
                a.download = ((gd._csv && gd._csv.filename) || "chart") + ".png";
                document.body.appendChild(a); a.click(); a.remove();
            });
        }
        const plotConfig = name => ({
            responsive: true, displaylogo: false, displayModeBar: true,     // toolbar always visible
            modeBarButtonsToRemove: ["lasso2d", "select2d", "toImage"],
            modeBarButtonsToAdd: [{ name: "Download plot as PNG", icon: Plotly.Icons.camera, click: savePlotPNG },
                                  { name: "Download data as CSV", icon: Plotly.Icons.disk, click: downloadCSV }]
        });
        // Line breaks: each curve keeps only its own non-empty values, and a null point is
        // inserted wherever two consecutive values are further apart than the threshold, so
        // the line breaks instead of joining across missing hours (e.g. AERONET at night).
        // Threshold = stations.gap_minutes for hourly data; a model with output_hours = N
        // gets (N - 1) * 60 + gap_minutes (e.g. 185 min for 3-hourly GEFS-Aerosols).
        const GAP_MIN = CFG.stations.gap_minutes ?? 65;
        const tms = t => Date.parse(t.replace(" ", "T") + "Z");                  // "YYYY-MM-DD HH:MM" -> ms
        const tPlus1 = t => new Date(tms(t) + 60000).toISOString().slice(0, 16).replace("T", " ");
        function curve(x0, y0, everyH) {
            const maxMs = ((everyH - 1) * 60 + GAP_MIN) * 60000, x = [], y = [];
            let prev = null;
            for (let i = 0; i < x0.length; i++) {
                if (y0[i] === null || y0[i] === undefined) continue;
                if (prev !== null && tms(x0[i]) - tms(prev) > maxMs) { x.push(tPlus1(prev)); y.push(null); }
                x.push(x0[i]); y.push(y0[i]);
                prev = x0[i];
            }
            return { x, y };
        }
        const spikeAxis = { showspikes: true, spikemode: "across", spikesnap: "cursor",
                            spikethickness: 1, spikedash: "dash", spikecolor: "#555555" };

        // ======================= STATION CHARTS =======================
        function renderStationPlot(div, sid) {
            const payload = DATA[activePlatform], st = payload && payload.stations[sid];
            if (!st) return;
            const v = activeVariable, e = st.d[v];
            const legendBox = div.parentElement.querySelector(".plot-legend");
            if (!e) {                                       // no data for this variable
                Plotly.purge(div);
                if (legendBox) legendBox.innerHTML = "";
                div.innerHTML = `<div class="plot-msg"><h3>${st.name}</h3>
                    <p>No <b>${v}</b> data available for this ${activePlatform} station.</p></div>`;
                return;
            }
            if (div.querySelector(".plot-msg")) div.innerHTML = "";
            const cols = payload.species[v].columns, x0 = e.t || payload.species[v].time;
            const traces = [], items = [];
            traceSettings().forEach(it => {
                const k = cols.indexOf(it.id), y0 = k >= 0 ? e.v[k] : null;
                if (!it.visible || !y0) return;             // unchecked, or no column for this model
                const { x, y } = curve(x0, y0, it.every);
                const tr = { x, y, name: it.name, type: "scatter", mode: it.isObs ? "lines+markers" : "lines",
                             visible: hiddenTraces.has(it.id) ? "legendonly" : true,     // hidden in the legend panel
                             line: { color: it.color, dash: it.style, width: it.isObs ? 2.2 : 1.7 } };
                items.push(it);
                if (it.isObs) tr.marker = { size: 5, color: it.color };   // never pass marker: undefined
                traces.push(tr);
            });
            const fname = `${activePlatform}_${sid}_${v.replace(/\s+/g, "_")}`;
            const title = coords => ({ text: `<b>${st.name}</b>${coords} (${activePlatform}) — ${v}`,
                                       font: { size: 14, color: "#0f172a" }, x: 0.02, xanchor: "left", y: 0.97 });
            const layout = {
                title: title(""),
                xaxis: { title: { text: "UTC Time", standoff: 6 }, automargin: true,      // clear of the date labels
                         range: CFG.window, ...spikeAxis },                                // always the full 48 h
                yaxis: { title: v + (UNITS[v] ? ` (${UNITS[v]})` : ""), hoverformat: `.${decOf(v)}f` },
                hovermode: "x unified",
                showlegend: false,                          // legend = the panel next to the chart
                margin: { l: 55, r: 25, t: 45, b: 45 },
                autosize: true
            };
            Plotly.react(div, traces, layout, plotConfig(fname));
            div._items = items;
            // PNG: coordinates after the station name, legend at the bottom
            div._png = { legend: true, layout: { title: title(`  ${deg(st.lat, "N", "S", 2)} ${deg(st.lon, "E", "W", 2)}`),
                                                 margin: { l: 60, r: 25, t: 45, b: 45 } } };
            if (legendBox) buildChartLegend(div, legendBox);
            div._csv = { filename: fname, header: [
                `Station: ${st.name} (id ${sid}), platform ${activePlatform}`,
                `Lat ${st.lat}, Lon ${st.lon}`, `Variable: ${v} [${UNITS[v] || ""}]`, `Cycle: ${CFG.cycle}`] };
        }
        // ---- legend panel of the station chart (click: show/hide, double-click: only this one) ----
        // Grouped by model; hidden curves are remembered while the popup is open.
        let hiddenTraces = new Set();     // ids of curves hidden in the legend panel
        const DASH_SVG = { solid: "", dash: "6,3", dot: "1.5,2.5", dashdot: "6,2.5,1.5,2.5" };
        const lineSym = it => `<svg width="28" height="10"><line x1="1" y1="5" x2="27" y2="5" stroke="${it.color}" ` +
            `stroke-width="${it.isObs ? 2.2 : 1.8}" stroke-dasharray="${DASH_SVG[it.style] || ""}"/>` +
            (it.isObs ? `<circle cx="14" cy="5" r="3" fill="${it.color}"/>` : "") + `</svg>`;
        function buildChartLegend(div, box) {
            const items = div._items || [];
            if (!items.length) { box.innerHTML = `<div class="hint">No curves selected in the left panel</div>`; return; }
            let h = "", grp = null;
            items.forEach((it, i) => {
                if (!it.isObs && it.group !== grp) {
                    grp = it.group;
                    h += `<div class="lg-grp" data-grp="${grp}" title="Click: show/hide all ${grp} runs">${grp}</div>`;
                }
                h += `<div class="lg-row" data-i="${i}" title="${it.name}\nClick: show/hide · Double-click: only this one">` +
                     `${lineSym(it)}<span>${it.isObs ? it.name : it.run}</span></div>`;
            });
            box.innerHTML = h + `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
            syncChartLegend(div, box);
            if (box._wired) return;                 // listeners once per popup
            box._wired = true;
            const chart = () => box.parentElement.querySelector(".plot-box");
            const apply = () => {
                const d = chart(), its = d._items || [];
                Plotly.restyle(d, { visible: its.map(it => (hiddenTraces.has(it.id) ? "legendonly" : true)) });
                syncChartLegend(d, box);
            };
            box.addEventListener("click", e => {
                const its = chart()._items || [];
                const row = e.target.closest(".lg-row"), g = e.target.closest(".lg-grp");
                if (e.target.closest(".show-all")) hiddenTraces.clear();
                else if (row) {
                    const id = its[+row.dataset.i].id;
                    hiddenTraces.has(id) ? hiddenTraces.delete(id) : hiddenTraces.add(id);
                } else if (g) {                          // model header: all its runs off, or all on
                    const ids = its.filter(it => it.group === g.dataset.grp && !it.isObs).map(it => it.id);
                    const anyOn = ids.some(id => !hiddenTraces.has(id));
                    ids.forEach(id => (anyOn ? hiddenTraces.add(id) : hiddenTraces.delete(id)));
                } else return;
                apply();
            });
            box.addEventListener("dblclick", e => {    // both clicks already toggled it back
                const row = e.target.closest(".lg-row");
                if (!row) return;
                const its = chart()._items || [], id = its[+row.dataset.i].id;
                const alone = its.every(it => (it.id === id) !== hiddenTraces.has(it.id));
                hiddenTraces = alone ? new Set() : new Set(its.filter(it => it.id !== id).map(it => it.id));
                apply();
            });
        }
        function syncChartLegend(div, box) {   // restyle in place (rebuilding would break double-click)
            const its = div._items || [];
            box.querySelectorAll(".lg-row").forEach(r => r.classList.toggle("off", hiddenTraces.has(its[+r.dataset.i].id)));
            box.querySelectorAll(".lg-grp").forEach(g => {     // model header off = all its runs off
                const ids = its.filter(it => it.group === g.dataset.grp && !it.isObs).map(it => it.id);
                g.classList.toggle("off", ids.length > 0 && ids.every(id => hiddenTraces.has(id)));
            });
        }
        function refreshCurrentPlot() {   // re-draw the open station chart after a panel change
            if (!openStationKey) return;
            const div = document.querySelector(".leaflet-popup .plot-box[data-sid]");
            if (div) renderStationPlot(div, openStationKey);
        }

        // ======================= STATION MARKERS =======================
        // Variable with a colour scale  -> same-size circles coloured by the 48-h max observation
        // Variable without a colour scale -> default drop markers, clustered
        // ONE canvas for station circles AND fire symbols. Two separate canvases would
        // stack on top of each other and the upper one would swallow every click,
        // leaving the markers drawn on the lower one unclickable.
        const canvasRenderer = L.canvas({ padding: 0.5, tolerance: 3 });
        const stationLayer = L.layerGroup().addTo(map);
        let stationMarkers = {};          // station id -> marker (active platform)
        let colorMode = false;
        let openStationKey = null;

        function binIndex(v, scale) {     // last lower edge <= value; "no data" = colors.length
            if (v === null || v === undefined) return scale.colors.length;
            let i = 0;
            while (i + 1 < scale.bounds.length && v >= scale.bounds[i + 1]) i++;
            return i;
        }
        function scaleColor(v, scale) {
            const i = binIndex(v, scale);
            return i === scale.colors.length ? NO_DATA_COLOR : scale.colors[i];
        }
        // Legend filter per variable (last entry = "no data", hidden by default)
        const binVisible = {};
        const binVis = v => (binVisible[v] ||= [...Array(COLOR_SCALES[v].colors.length).fill(true), false]);
        const hasObs = st => st.obs_max[activeVariable] !== null && st.obs_max[activeVariable] !== undefined;
        const stationShown = st => binVis(activeVariable)[binIndex(st.obs_max[activeVariable], COLOR_SCALES[activeVariable])];

        function applyStationFilter() {   // add/remove only what changed (keeps an open popup)
            if (!colorMode) return;
            const S = stationsOf(activePlatform);
            Object.entries(stationMarkers).forEach(([k, m]) => {
                const show = stationShown(S[k]), on = stationLayer.hasLayer(m);
                if (show && !on) stationLayer.addLayer(m);
                else if (!show && on) stationLayer.removeLayer(m);
            });
        }
        function stationTooltip(st) {
            const mx = st.obs_max[activeVariable];
            const val = mx === null || mx === undefined ? "no data" : `max obs. ${fmtVal(mx, activeVariable)}${unitOf(activeVariable)}`;
            return `<b>${st.name}</b><br>${activeVariable}: ${val}`;
        }
        function clearStations() {
            map.closePopup();
            markerCluster.clearLayers();
            stationLayer.clearLayers();
            stationMarkers = {};
            colorLegend.remove();
            showStatus("");
        }
        function updateMarkers() {
            const plat = activePlatform;
            const reopen = openStationKey;
            clearStations();
            if (!plat || !activeVariable) return;       // nothing selected: reference layers only
            const S = stationsOf(plat);
            if (!Object.keys(S).length) showStatus(`No ${plat} stations for this cycle`);
            const scale = COLOR_SCALES[activeVariable];
            colorMode = !!scale;
            Object.entries(S).forEach(([sid, st]) => {
                const marker = scale
                    ? L.circleMarker([st.lat, st.lon], {
                          renderer: canvasRenderer, radius: CFG.stations.marker_radius, color: "#111111", weight: 1,
                          fillColor: scaleColor(st.obs_max[activeVariable], scale), fillOpacity: 0.95 })
                    : L.marker([st.lat, st.lon], { title: st.name });
                marker.bindTooltip(() => stationTooltip(st));
                marker.bindPopup(smartPopup(`<div class="plot-wrap"><div class="plot-box" data-sid="${sid}"></div>` +
                                            `<div class="plot-legend"></div></div>`));
                marker.on("popupopen", e => {
                    if (openStationKey !== sid) hiddenTraces = new Set();   // new station: all curves on
                    openStationKey = sid;
                    renderStationPlot(e.popup.getElement().querySelector(".plot-box"), sid);
                });
                marker.on("popupclose", e => {
                    if (openStationKey === sid) openStationKey = null;
                    const el = e.popup.getElement();
                    if (el) Plotly.purge(el.querySelector(".plot-box"));   // free the chart's memory
                });
                if (!scale) { if (hasObs(st)) markerCluster.addLayer(marker); }     // no-data stations hidden
                else if (stationShown(st)) stationLayer.addLayer(marker);
                stationMarkers[sid] = marker;
            });
            updateColorLegend();
            if (reopen && stationMarkers[reopen]) stationMarkers[reopen].openPopup();
        }
        function recolorStations() {
            const scale = COLOR_SCALES[activeVariable], S = stationsOf(activePlatform);
            Object.entries(stationMarkers).forEach(([k, m]) => {
                if (m.setStyle) m.setStyle({ fillColor: scaleColor(S[k].obs_max[activeVariable], scale) });
            });
            applyStationFilter();
            updateColorLegend();
        }

        // ---- colour legend (click a bin: show/hide; double-click: only that bin) ----
        const colorLegend = L.control({ position: "bottomleft" });
        colorLegend.onAdd = () => {
            const d = L.DomUtil.create("div", "color-legend");
            L.DomEvent.disableClickPropagation(d);
            L.DomEvent.disableScrollPropagation(d);
            const vis = () => binVis(activeVariable);
            d.addEventListener("click", e => {
                if (e.target.closest(".show-all")) vis().fill(true);
                else {
                    const row = e.target.closest(".bin-row");
                    if (!row) return;
                    vis()[+row.dataset.i] = !vis()[+row.dataset.i];
                }
                applyStationFilter(); syncLegendRows();
            });
            d.addEventListener("dblclick", e => {
                const row = e.target.closest(".bin-row");
                if (!row) return;
                const v = vis();                            // both clicks already toggled it back
                v.forEach((_, i) => v[i] = (i === +row.dataset.i));
                applyStationFilter(); syncLegendRows();
            });
            return d;
        };
        function syncLegendRows() {       // restyle in place (rebuilding would break double-click)
            const vis = binVis(activeVariable);
            document.querySelectorAll(".color-legend .bin-row").forEach(r => r.classList.toggle("off", !vis[+r.dataset.i]));
        }
        function updateColorLegend() {
            const scale = COLOR_SCALES[activeVariable];
            if (!(colorMode && scale && Object.keys(stationMarkers).length)) { colorLegend.remove(); return; }
            if (!colorLegend._map) colorLegend.addTo(map);
            const b = scale.bounds, u = unitOf(activeVariable), vis = binVis(activeVariable), S = stationsOf(activePlatform);
            const cnt = Array(scale.colors.length + 1).fill(0);
            Object.keys(stationMarkers).forEach(k => cnt[binIndex(S[k].obs_max[activeVariable], scale)]++);
            const row = (i, color, lbl) =>
                `<div class="row bin-row${vis[i] ? "" : " off"}" data-i="${i}" title="Click: show/hide · Double-click: only this one">` +
                `<span class="sw" style="background:${color}"></span>${lbl}<span class="cnt">${cnt[i]}</span></div>`;
            let h = `<b>${activeVariable} — max obs. (48 h)</b>`;
            scale.colors.forEach((c, i) => {
                // bounds shown as written in config.yaml
                const lbl = i === b.length - 1 ? `≥ ${b[i]}` : i === 0 ? `< ${b[1]}` : `${b[i]} – ${b[i + 1]}`;
                h += row(i, c, lbl + u);
            });
            h += row(scale.colors.length, NO_DATA_COLOR, "no data");
            h += `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
            colorLegend.getContainer().innerHTML = h;
        }

        // ======================= HMS FIRES (canvas) =======================
        // Leaflet's canvas only knows circles, so add squares and triangles.
        L.Canvas.include({
            _updateShape(layer) {
                if (!this._drawing || layer._empty()) return;
                const p = layer._point, ctx = this._ctx, r = Math.max(Math.round(layer._radius), 1);
                ctx.beginPath();
                if (layer.options.shape === "square") ctx.rect(p.x - r, p.y - r, 2 * r, 2 * r);
                else if (layer.options.shape === "triangle") {
                    const h = r * 1.25;
                    ctx.moveTo(p.x, p.y - h); ctx.lineTo(p.x + h, p.y + 0.8 * h); ctx.lineTo(p.x - h, p.y + 0.8 * h);
                    ctx.closePath();
                } else ctx.arc(p.x, p.y, r, 0, 2 * Math.PI);
                this._fillStroke(ctx, layer);
            }
        });
        const ShapeMarker = L.CircleMarker.extend({
            options: { shape: "circle" },
            _updatePath() { this._renderer._updateShape(this); }
        });
        const FRP_EDGES = CFG.hms.frp_classes, RADII = CFG.hms.radii;
        function fireRadius(frp) {        // marker half-size (px) from the 48-h max FRP
            if (frp === null) return RADII[0];
            let i = 0;
            while (i < FRP_EDGES.length && frp >= FRP_EDGES[i]) i++;
            return RADII[i];
        }
        const fireLayer = L.layerGroup();
        const SAT_KEYS = [...Object.keys(SAT_STYLE), "Other"];
        const satCat = sat => (sat in SAT_STYLE ? sat : "Other");
        const satVisible = Object.fromEntries(SAT_KEYS.map(k => [k, true]));
        const satCount = Object.fromEntries(SAT_KEYS.map(k => [k, 0]));
        const FIRE_P = REF.hms;
        let fireMarkers = null;           // built the first time the fire layer is shown

        const fireTimeStr = (F, mins) => new Date(Date.parse(F.t0) + mins * 60000).toISOString().slice(0, 16).replace("T", " ");
        function fireCard(F, i, frpTxt, tStr) {
            const n = F.n[i];
            const rows = [
                ["Lat", deg(F.lat[i], "N", "S")], ["Lon", deg(F.lon[i], "E", "W")],
                ["Satellite", F.sat[i]], ["Method", F.methods[F.m[i]]], ["Ecosystem", F.eco[i]],
                [n > 1 ? "Max FRP" : "FRP", frpTxt], [n > 1 ? "First obs." : "Time", `${tStr} UTC`]
            ];
            return `<div class="fire-card"><h4>${n > 1 ? `HMS fire — ${n} detections` : "HMS fire — single detection"}</h4><table>` +
                   rows.map(([k, v]) => `<tr><td>${k}</td><td><b>${v}</b></td></tr>`).join("") + `</table></div>`;
        }
        function renderFirePlot(div, i) {
            const F = FIRE_P.fires, s = FIRE_P.series[i];
            const sty = SAT_STYLE[F.sat[i]] || SAT_OTHER;
            const x = s.t.map(m => fireTimeStr(F, m));
            const valid = s.frp.some(v => v !== null);
            const trace = {
                x, y: valid ? s.frp : x.map(() => 0), type: "scatter", mode: "lines+markers", name: "FRP (MW)",
                connectgaps: false, line: { color: "#444444", width: 1 },
                marker: { size: 8, color: sty.color, line: { color: "#222222", width: 1 },
                          symbol: { square: "square", circle: "circle", triangle: "triangle-up" }[sty.shape] },
                hovertemplate: valid ? `%{y:.${decOf("FRP")}f} MW<extra></extra>` : "detected (FRP n/a)<extra></extra>"
            };
            const info = [`Lat: ${deg(F.lat[i], "N", "S")}`, `Lon: ${deg(F.lon[i], "E", "W")}`, `Satellite: ${F.sat[i]}`,
                          `Method: ${F.methods[F.m[i]]}`, `Ecosystem: ${F.eco[i]}`].join("  ·  ");
            const sub = `${F.n[i]} detections · max FRP ` + (F.frp[i] === null ? "n/a" : `${F.frp[i]} MW`);
            const fname = `hms_fire_${F.lat[i].toFixed(3)}_${F.lon[i].toFixed(3)}`;
            const layout = {
                title: { text: `<b>HMS fire — Fire Radiative Power</b><br><span style="font-size:12px">${info}</span>` +
                               `<br><span style="font-size:11px;color:#555">${sub}</span>`,
                         font: { size: 14, color: "#0f172a" }, x: 0.02, xanchor: "left", y: 0.97 },
                xaxis: { title: "UTC Time", range: [fireTimeStr(F, 0), fireTimeStr(F, F.minutes)], ...spikeAxis },
                yaxis: { title: "FRP (MW)", rangemode: "tozero", showticklabels: valid },
                hovermode: "x unified", showlegend: false, margin: { l: 60, r: 25, t: 95, b: 45 }, autosize: true,
                annotations: valid ? [] : [{ text: "FRP not available for this detection method", xref: "paper", yref: "paper",
                                             x: 0.5, y: 0.5, showarrow: false, font: { size: 13, color: "#777" } }]
            };
            Plotly.newPlot(div, [trace], layout, plotConfig(fname));
            div._csv = { filename: fname, blankY: !valid, header: [
                `HMS fire: Lat ${F.lat[i]}, Lon ${F.lon[i]}`, `Satellite: ${F.sat[i]}, Method: ${F.methods[F.m[i]]}, Ecosystem: ${F.eco[i]}`,
                `FRP in MW${valid ? "" : " (not available for this detection method)"}`, `Cycle: ${CFG.cycle}`] };
        }

        function buildFires() {
            if (fireMarkers || !FIRE_P.fires.lon.length) return;
            const F = FIRE_P.fires;
            fireMarkers = [];
            for (let i = 0; i < F.lon.length; i++) {        // sorted small -> large FRP
                const sat = F.sat[i], frp = F.frp[i], sty = SAT_STYLE[sat] || SAT_OTHER;
                const m = new ShapeMarker([F.lat[i], F.lon[i]], {
                    renderer: canvasRenderer, shape: sty.shape, radius: fireRadius(frp),
                    color: "#1a1a1a", weight: 0.7, fillColor: sty.color, fillOpacity: 0.9
                });
                const frpTxt = frp === null ? "n/a" : `${frp} MW`;
                if (F.t[i] === null) {                      // GOES fire with a time series
                    m.bindTooltip(`Max FRP = ${frpTxt}, detected by ${sat} (${F.n[i]} obs.)`);
                    m.bindPopup(smartPopup(`<div class="plot-box"></div>`));
                    m.on("popupopen", e => renderFirePlot(e.popup.getElement().querySelector(".plot-box"), i));
                    m.on("popupclose", e => { const el = e.popup.getElement(); if (el) Plotly.purge(el.querySelector(".plot-box")); });
                } else {                                    // info card only
                    const tStr = fireTimeStr(F, F.t[i]);
                    m.bindTooltip(F.n[i] > 1
                        ? `Max FRP = ${frpTxt}, detected by ${sat} (${F.n[i]} obs., first ${tStr} UTC)`
                        : `FRP = ${frpTxt}, detected by ${sat} at ${tStr} UTC`);
                    m.bindPopup(smartPopup(() => fireCard(F, i, frpTxt, tStr)));
                }
                const cat = satCat(sat);
                fireMarkers.push({ m, cat });
                satCount[cat]++;
            }
            refreshFires();
        }
        function refreshFires() {         // re-add visible satellites, keeping the FRP draw order
            if (!fireMarkers) return;
            fireLayer.clearLayers();
            fireMarkers.forEach(f => { if (satVisible[f.cat]) fireLayer.addLayer(f.m); });
            document.querySelectorAll(".fire-legend .sat-row").forEach(r => r.classList.toggle("off", !satVisible[r.dataset.sat]));
        }

        const sym = (shape, color, r) => {
            const s = 2 * r + 4, c = s / 2;
            const g = shape === "square" ? `<rect x="${c - r}" y="${c - r}" width="${2 * r}" height="${2 * r}"/>`
                    : shape === "triangle" ? `<polygon points="${c},${c - 1.25 * r} ${c + 1.25 * r},${c + r} ${c - 1.25 * r},${c + r}"/>`
                    : `<circle cx="${c}" cy="${c}" r="${r}"/>`;
            return `<svg width="${s}" height="${s}"><g fill="${color}" stroke="#1a1a1a" stroke-width="0.7">${g}</g></svg>`;
        };
        const fireLegend = L.control({ position: "bottomleft" });
        fireLegend.onAdd = () => {
            const d = L.DomUtil.create("div", "fire-legend");
            let h = "<b>HMS fires</b>";
            SAT_KEYS.forEach(k => {
                const v = SAT_STYLE[k] || SAT_OTHER;
                h += `<div class="row sat-row${satVisible[k] ? "" : " off"}" data-sat="${k}" title="Click: show/hide · Double-click: only this one">` +
                     `${sym(v.shape, v.color, 5)}${k}<span class="cnt">${satCount[k]}</span></div>`;
            });
            h += `<div class="hint">click: show/hide · double-click: only · <span class="show-all">show all</span></div>`;
            h += "<b style='margin-top:6px'>Max FRP (48 h)</b>";
            const E = FRP_EDGES;
            [`< ${E[0]} MW`, ...E.slice(0, -1).map((e, k) => `${e}–${E[k + 1]} MW`), `> ${E[E.length - 1]} MW`]
                .forEach((t, k) => h += `<div class="row">${sym("circle", "#bbbbbb", RADII[k])}${t}</div>`);
            d.innerHTML = h;
            L.DomEvent.disableClickPropagation(d);
            L.DomEvent.disableScrollPropagation(d);
            d.addEventListener("click", e => {
                if (e.target.closest(".show-all")) { SAT_KEYS.forEach(k => satVisible[k] = true); return refreshFires(); }
                const row = e.target.closest(".sat-row");
                if (!row) return;
                satVisible[row.dataset.sat] = !satVisible[row.dataset.sat];
                refreshFires();
            });
            d.addEventListener("dblclick", e => {
                const row = e.target.closest(".sat-row");
                if (!row) return;
                SAT_KEYS.forEach(k => satVisible[k] = (k === row.dataset.sat));
                refreshFires();
            });
            return d;
        };
        // Fire layer shown (HMS platform or the overlay checkbox) -> build markers once + legend
        fireLayer.on("add", () => { buildFires(); fireLegend.addTo(map); });
        fireLayer.on("remove", () => fireLegend.remove());

        // ======================= SATELLITE AOD: VIIRS + TEMPO (image overlays) =======================
        // Each field arrives as an 8-bit PNG on its native lat/lon grid (row 0 = north;
        // code 0 = missing, AOD = vmax * ((code - 1) / 254)^2). Leaflet stretches an image
        // overlay linearly in Web Mercator, so the rows are re-spaced here: every output row
        // is a slice of equal Mercator height, filled with the grid row that contains its
        // latitude. Rows are only repeated, never skipped.
        // Both layers share ONE time line (slider at the bottom of the map): for the selected
        // hour each layer shows its own frame (nothing if it has no data then), or its own mean.
        const aodPane = map.createPane("aodPane");
        aodPane.style.zIndex = 250;                 // above the base map (200), below boundaries,
        aodPane.style.pointerEvents = "none";       // stations and fires (400+); never takes clicks
        const BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
        const hoverBox = document.getElementById("aod-hover");
        const hideHover = () => { hoverBox.style.display = "none"; };
        const AD = CFG.ref.aodDisplay;              // shared: start frame, opacity, animation speed
        const T0 = tms(CFG.window[0]);
        const NSLOT = Math.round((tms(CFG.window[1]) - T0) / 3600000) + 1;      // hourly slots, both ends included
        const slotLabel = s => new Date(T0 + s * 3600000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
        let aodSel = "mean";                        // "mean" or slot = hours after the window start

        // PNG decoded here, byte for byte: chunks -> zlib (DecompressionStream) -> undo the
        // row filters. The browser's own image decoder is NOT used for the values: Safari
        // colour-converts with dithering, turning some 0 (missing) bytes into 1 (AOD 0).
        async function decodePNG(bytes) {
            const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), idat = [];
            let p = 8, w = 0, h = 0;
            while (p + 8 <= bytes.length) {
                const len = dv.getUint32(p), type = String.fromCharCode(...bytes.subarray(p + 4, p + 8));
                if (type === "IHDR") { w = dv.getUint32(p + 8); h = dv.getUint32(p + 12); }
                else if (type === "IDAT") idat.push(bytes.subarray(p + 8, p + 8 + len));
                else if (type === "IEND") break;
                p += 12 + len;                              // length + type + data + CRC
            }
            const stream = new Blob(idat).stream().pipeThrough(new DecompressionStream("deflate"));
            const raw = new Uint8Array(await new Response(stream).arrayBuffer());
            const out = new Uint8Array(w * h);
            for (let r = 0; r < h; r++) {                   // 8-bit gray: 1 filter byte + w bytes per row
                const f = raw[r * (w + 1)], src = r * (w + 1) + 1, dst = r * w;
                for (let j = 0; j < w; j++) {
                    const x = raw[src + j], a = j ? out[dst + j - 1] : 0, b = r ? out[dst - w + j] : 0,
                          c = j && r ? out[dst - w + j - 1] : 0;
                    let v = x;                              // 0 None
                    if (f === 1) v = x + a;                 // Sub
                    else if (f === 2) v = x + b;            // Up
                    else if (f === 3) v = x + ((a + b) >> 1);   // Average
                    else if (f === 4) {                     // Paeth
                        const q = a + b - c, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c);
                        v = x + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
                    }
                    out[dst + j] = v & 255;
                }
            }
            return out;
        }

        // One AOD layer (key = "viirs" | "tempo"); null when the page was built without it
        function makeAodLayer(key, zIndex) {
            const VI = REF[key], VC = CFG.ref[key];
            if (!VI) return null;
            const G = VI.grid, D2R = Math.PI / 180;
            const dLon = (G.east - G.west) / G.nx, dLat = (G.north - G.south) / G.ny;
            const mercY = lat => Math.log(Math.tan(Math.PI / 4 + lat * D2R / 2));
            const yN = mercY(G.north), yS = mercY(G.south);
            const latMin = G.south <= 0 && G.north >= 0 ? 0 : Math.min(Math.abs(G.south), Math.abs(G.north));
            const NYM = Math.min(Math.ceil((yN - yS) / (dLat * D2R / Math.cos(latMin * D2R))), 8 * G.ny);
            const rowMap = new Int32Array(NYM);     // output (Mercator) row -> grid row
            for (let r = 0; r < NYM; r++) {
                const lat = Math.atan(Math.sinh(yN - (r + 0.5) * (yN - yS) / NYM)) / D2R;
                rowMap[r] = Math.min(G.ny - 1, Math.max(0, Math.floor((G.north - lat) / dLat)));
            }
            // code -> RGBA (one Uint32 per pixel, little-endian ABGR); code 0 stays transparent
            const codeVal = c => VI.vmax * ((c - 1) / 254) ** 2;
            const LUT = new Uint32Array(256);
            for (let c = 1; c < 256; c++) {
                const hx = scaleColor(codeVal(c), VC.scale);
                const [r, g, b] = [1, 3, 5].map(k => parseInt(hx.slice(k, k + 2), 16));
                LUT[c] = ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0;
            }
            const codeCache = {}, urlCache = {};
            async function frameCodes(k) {          // PNG -> Uint8Array of codes (cached)
                if (codeCache[k]) return codeCache[k];
                const bytes = Uint8Array.from(atob(VI.frames[k].png), ch => ch.charCodeAt(0));
                if (typeof DecompressionStream !== "undefined") return (codeCache[k] = await decodePNG(bytes));
                // Very old browsers only (no DecompressionStream): browser decoder, may be inexact
                const blob = new Blob([bytes], { type: "image/png" });
                let bmp;                            // no colour management: keep the raw byte values
                try { bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" }); }
                catch (e) { bmp = await createImageBitmap(blob); }
                const cv = document.createElement("canvas");
                cv.width = G.nx; cv.height = G.ny;
                const ctx = cv.getContext("2d", { willReadFrequently: true });
                ctx.drawImage(bmp, 0, 0);
                const px = ctx.getImageData(0, 0, G.nx, G.ny).data, codes = new Uint8Array(G.nx * G.ny);
                for (let i = 0; i < codes.length; i++) codes[i] = px[4 * i];
                return (codeCache[k] = codes);
            }
            async function frameURL(k) {            // coloured, Mercator-spaced image (cached)
                if (urlCache[k]) return urlCache[k];
                const codes = await frameCodes(k);
                const cv = document.createElement("canvas");
                cv.width = G.nx; cv.height = NYM;
                const ctx = cv.getContext("2d"), img = ctx.createImageData(G.nx, NYM);
                const out = new Uint32Array(img.data.buffer);
                for (let r = 0; r < NYM; r++) {
                    const src = rowMap[r] * G.nx, dst = r * G.nx;
                    for (let j = 0; j < G.nx; j++) out[dst + j] = LUT[codes[src + j]];
                }
                ctx.putImageData(img, 0, 0);
                const blob = await new Promise(res => cv.toBlob(res, "image/png"));
                return (urlCache[k] = URL.createObjectURL(blob));
            }
            const layer = L.imageOverlay(BLANK, [[G.south, G.west], [G.north, G.east]],
                                         { pane: "aodPane", opacity: AD.opacity, interactive: false,
                                           className: "aod-img", zIndex });
            const bySlot = {};                      // slot -> frame index (frame 0 = mean)
            VI.frames.forEach((f, k) => { if (!f.mean) bySlot[f.h] = k; });
            const A = { key, VI, VC, layer, codes: null, tok: 0,
                        slots: Object.keys(bySlot).map(Number).sort((a, b) => a - b) };
            A.frameAt = sel => (sel === "mean" ? 0 : bySlot[sel]);        // undefined = no data that hour
            A.render = async sel => {               // show the frame of sel (or nothing)
                const tok = ++A.tok, k = A.frameAt(sel);
                if (!map.hasLayer(layer)) return;
                if (k === undefined) { A.codes = null; layer.setUrl(BLANK); return; }
                const url = await frameURL(k), codes = await frameCodes(k);
                if (tok !== A.tok || !map.hasLayer(layer)) return;     // a newer frame was picked meanwhile
                A.codes = codes;
                layer.setUrl(url);
            };
            A.prefetch = sel => { const k = A.frameAt(sel); if (k !== undefined) frameURL(k); };
            A.paint = P => {                        // current frame -> canvas in projection P (nearest cell)
                const w = P.pw, h = P.ph, cv = document.createElement("canvas");
                cv.width = w; cv.height = h;
                if (!A.codes) return cv;
                const ctx = cv.getContext("2d"), img = ctx.createImageData(w, h), out = new Uint32Array(img.data.buffer);
                for (let y = 0; y < h; y++) {
                    for (let x = 0; x < w; x++) {
                        const ll = P.inv(x, y);     // output pixel -> lon, lat
                        if (!ll) continue;
                        const lon = ((ll[0] + 180) % 360 + 360) % 360 - 180;
                        const i = Math.floor((G.north - ll[1]) / dLat), j = Math.floor((lon - G.west) / dLon);
                        if (i >= 0 && i < G.ny && j >= 0 && j < G.nx) out[y * w + x] = LUT[A.codes[i * G.nx + j]];
                    }
                }
                ctx.putImageData(img, 0, 0);
                return cv;
            };
            A.valueAt = ll => {                     // AOD under the cursor (native grid) or null
                if (!A.codes) return null;
                const i = Math.floor((G.north - ll.lat) / dLat), j = Math.floor((ll.lng - G.west) / dLon);
                const c = i >= 0 && i < G.ny && j >= 0 && j < G.nx ? A.codes[i * G.nx + j] : 0;
                return c ? codeVal(c) : null;
            };
            return A;
        }
        const AOD_KEYS = ["viirs", "tempo"];                      // draw order: TEMPO above VIIRS
        const AODS = AOD_KEYS.map((k, i) => makeAodLayer(k, i + 1)).filter(Boolean);
        const aodOn = () => AODS.filter(A => map.hasLayer(A.layer));

        // ---- time slider (centre bottom of the map, while an AOD layer is on) ----
        // Only hours with data in a shown layer can be selected: dragging or the arrow keys
        // jump to the next hour with data. "Mean" shows each layer's mean of the window.
        const tsBox = document.getElementById("aod-slider"), tsRange = document.getElementById("aod-range");
        const tsTicks = document.getElementById("aod-ticks"), tsLbl = document.getElementById("aod-time-lbl");
        const tsMean = document.getElementById("aod-mean"), tsPlay = document.getElementById("aod-play");
        L.DomEvent.disableClickPropagation(tsBox);
        L.DomEvent.disableScrollPropagation(tsBox);
        tsRange.max = NSLOT - 1;
        let slots = [], playing = false, playTok = 0;            // slots = hours with data (shown layers)
        let dragging = false, lastV = 0;                          // thumb held with the mouse/finger
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        function nearestSlot(v, dir) {      // dir > 0: next at/after v; dir < 0: last at/before v; 0: closest
            if (!slots.length) return null;
            if (dir > 0) return slots.find(s => s >= v) ?? slots[slots.length - 1];
            if (dir < 0) return [...slots].reverse().find(s => s <= v) ?? slots[0];
            return slots.reduce((b, s) => (Math.abs(s - v) < Math.abs(b - v) ? s : b));
        }
        function syncSliderUI() {           // buttons, label and thumb from aodSel
            const mean = aodSel === "mean";
            tsMean.classList.toggle("active", mean);
            tsBox.classList.toggle("mean", mean);
            if (!mean && !dragging) tsRange.value = aodSel;     // never move the thumb under the pointer
            tsLbl.textContent = mean ? `Mean of ${NSLOT - 1} h` : slotLabel(aodSel);
            tsPlay.textContent = playing ? "❚❚" : "▶";
            tsPlay.title = playing ? "Pause" : "Play the hours with data";
        }
        // Slider between the bottom-left and bottom-right legends when they leave room for it;
        // otherwise it spans the bottom and the legends move up above it.
        const ctlL = map.getContainer().querySelector(".leaflet-bottom.leaflet-left");
        const ctlR = map.getContainer().querySelector(".leaflet-bottom.leaflet-right");
        function layoutSlider() {
            ctlL.style.bottom = ctlR.style.bottom = "";
            tsBox.style.left = tsBox.style.width = "";
            if (tsBox.hidden) return;
            const W = map.getSize().x, lw = ctlL.offsetWidth;
            const rw = Math.max(0, ...[...ctlR.children].filter(c => !c.classList.contains("leaflet-control-attribution"))
                                                       .map(c => c.offsetWidth + 10));
            const gap = W - lw - rw - 30;
            if (gap >= 460) {
                const w = Math.min(816, gap);
                tsBox.style.width = w + "px";
                tsBox.style.left = (lw + 15 + gap / 2) + "px";          // centre of the free gap
            } else ctlL.style.bottom = ctlR.style.bottom = (tsBox.offsetHeight + 28) + "px";
        }
        if (window.ResizeObserver) {        // legends added/removed/resized, window resized
            const ro = new ResizeObserver(() => layoutSlider());
            [ctlL, ctlR, map.getContainer()].forEach(el => ro.observe(el));
        }
        function updateSlider() {           // after an AOD layer is switched on/off
            const on = aodOn();
            tsBox.hidden = !on.length;
            layoutSlider();
            if (!on.length) { stopPlay(); syncSliderUI(); return; }
            slots = [...new Set(on.flatMap(A => A.slots))].sort((a, b) => a - b);
            tsTicks.innerHTML = slots.map(s => `<span style="left:${s / (NSLOT - 1) * 100}%"></span>`).join("");
            tsPlay.disabled = !slots.length;
            if (aodSel !== "mean" && !slots.includes(aodSel)) aodSel = slots.length ? nearestSlot(aodSel, 0) : "mean";
            syncSliderUI();
        }
        async function setAodSel(sel) {
            aodSel = sel;
            hideHover();                    // value under the cursor belongs to the old frame
            syncSliderUI();
            updateAodLegend();
            await Promise.all(aodOn().map(A => A.render(sel)));
        }
        function stopPlay() {               // pause; the thumb stays where it is
            if (!playing) return;
            playing = false; playTok++;
            tsPlay.textContent = "▶"; tsPlay.title = "Play the hours with data";
        }
        async function play() {
            if (!slots.length) return;
            playing = true;
            const tok = ++playTok;
            syncSliderUI();
            if (aodSel === "mean") await setAodSel(slots[0]);
            while (playing && tok === playTok) {
                const i = slots.indexOf(aodSel), next = slots[(i + 1) % slots.length];    // loops back
                aodOn().forEach(A => A.prefetch(next));                                   // build it while waiting
                await sleep(AD.frame_ms);
                if (!playing || tok !== playTok) break;
                await setAodSel(next);
            }
        }
        tsPlay.onclick = () => (playing ? stopPlay() : play());
        tsMean.onclick = () => {
            stopPlay();
            setAodSel(aodSel === "mean" ? nearestSlot(+tsRange.value, 0) ?? "mean" : "mean");
        };
        // While dragging, the thumb follows the pointer freely and the map shows the hour with
        // data next to it (in the direction of the movement); on release the thumb snaps onto it.
        // Clicks on the track and arrow keys snap at once.
        tsRange.addEventListener("pointerdown", () => { dragging = true; lastV = aodSel === "mean" ? null : aodSel; });
        const endDrag = () => { if (dragging) { dragging = false; syncSliderUI(); } };
        window.addEventListener("pointerup", endDrag);
        window.addEventListener("pointercancel", endDrag);
        tsRange.addEventListener("change", endDrag);
        tsRange.addEventListener("input", () => {
            const v = +tsRange.value;                                  // read BEFORE anything resets the thumb
            stopPlay();
            const prev = dragging ? lastV : (aodSel === "mean" ? null : aodSel);
            const s = nearestSlot(v, prev === null ? 0 : Math.sign(v - prev));   // skip hours without data
            lastV = v;
            if (s === null) return;
            if (!dragging) tsRange.value = s;
            if (s !== aodSel) setAodSel(s); else syncSliderUI();
        });

        // ---- colour bar (bottom right, clear of the time slider): one bar per colour scale shown ----
        const aodLegend = L.control({ position: "bottomright" });
        aodLegend.onAdd = () => L.DomUtil.create("div", "aod-legend");
        function updateAodLegend() {
            const on = aodOn();
            if (!on.length) { aodLegend.remove(); return; }
            if (!aodLegend._map) aodLegend.addTo(map);
            const groups = [];
            on.forEach(A => {
                const id = JSON.stringify(A.VC.scale), g = groups.find(x => x.id === id);
                if (g) g.labels.push(A.VC.label); else groups.push({ id, scale: A.VC.scale, labels: [A.VC.label] });
            });
            const sub = aodSel === "mean" ? "Mean of the hours with data" : `${slotLabel(aodSel)} (±30 min)`;
            const n = Math.max(...groups.map(g => g.scale.colors.length));
            aodLegend.getContainer().style.width = Math.max(240, 24 * n + 24) + "px";   // ~24 px per bin
            aodLegend.getContainer().innerHTML = groups.map((g, gi) => {
                const S = g.scale, m = S.colors.length;
                return `<b>${g.labels.join(" · ")}</b>` + (gi === 0 ? `<div class="sub">${sub}</div>` : "") +
                       `<div class="cbar">${S.colors.map(c => `<span style="background:${c}"></span>`).join("")}</div>` +
                       `<div class="cbar-ticks">${S.bounds.slice(1).map((b, k) =>
                           `<span style="left:${(k + 1) / m * 100}%">${b}</span>`).join("")}</div>`;
            }).join("");
        }

        // ---- value under the cursor, read from the native grid of each shown layer ----
        let tipOpen = false;                        // a station/fire tooltip has priority
        map.on("tooltipopen", () => { tipOpen = true; hideHover(); });
        map.on("tooltipclose", () => { tipOpen = false; });
        map.on("mousemove", e => {
            const tgt = e.originalEvent && e.originalEvent.target, on = aodOn();
            if (!on.length || tipOpen || (tgt && tgt.closest && tgt.closest(".leaflet-popup, .leaflet-control"))) return hideHover();
            const parts = on.map(A => [A, A.valueAt(e.latlng)]).filter(([, v]) => v !== null);
            if (!parts.length) return hideHover();
            hoverBox.innerHTML = parts.map(([A, v]) => `${A.VC.label}: <b>${v.toFixed(A.VC.decimals)}</b>`).join("<br>");
            hoverBox.style.left = (e.containerPoint.x + 14) + "px";
            hoverBox.style.top = (e.containerPoint.y + 14) + "px";
            hoverBox.style.display = "block";
        });
        map.on("mouseout", hideHover);

        // ---- opacity (left panel), shared by both layers ----
        const opac = document.getElementById("aod-opacity");
        opac.value = AD.opacity;
        opac.addEventListener("input", () => AODS.forEach(A => A.layer.setOpacity(+opac.value)));

        // ---- Save the AOD as a PNG figure ----
        // Drawn from the data itself (not a screenshot): white background, the AOD of the
        // layer(s) shown (full colour, no transparency) and grey boundaries from Natural Earth
        // (no labels), whatever the light/dark mode. The save window picks the region (current
        // view or config reference.aod_snapshot.regions) and the projection. Nothing is
        // pre-computed: each output pixel looks up its grid cell through the inverse projection.
        const SNAP = CFG.ref.aodSnapshot;
        let boundaryGeo = null;                     // GeoJSON, fetched on the first save
        const loadBoundaries = () => (boundaryGeo ||= Promise.all(SNAP.boundaries.map(bd =>
            fetch(bd.url).then(r => (r.ok ? r.json() : null)).catch(() => null))));
        const D2R = Math.PI / 180;
        const wrap180 = d => ((d + 180) % 360 + 360) % 360 - 180;
        // Projection P for bounds bb = [west, east, south, north], W css px wide, scale SC:
        //   P.fwd(lon, lat) -> [x, y] css px;  P.inv(px, py) (device px) -> [lon, lat] or null
        function makeProj(kind, bb, W, SC, origin) {
            const [w, e, s, n] = bb;
            let fx, fy, ix, H, X0, X1, Y0, Y1;      // projected coords (x right, y up), inverse, frame
            if (kind === "latlon") {                // plate carree: equal degrees in x and y
                fx = (lon, lat) => lon; fy = (lon, lat) => lat;
                ix = (x, y) => [x, y];
                [X0, X1, Y0, Y1] = [w, e, s, n];
            } else if (kind === "mercator") {
                const my = lat => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * D2R / 2));
                fx = (lon, lat) => lon * D2R; fy = (lon, lat) => my(lat);
                ix = (x, y) => [x / D2R, Math.atan(Math.sinh(y)) / D2R];
                [X0, X1, Y0, Y1] = [w * D2R, e * D2R, my(s), my(n)];
            } else {                                // Lambert conformal conic, tangent at the origin latitude
                const [la0, lo0] = origin, p0 = la0 * D2R;
                const nn = Math.sin(p0), t = lat => Math.tan(Math.PI / 4 + Math.max(-89.9, Math.min(89.9, lat)) * D2R / 2);
                const F = Math.cos(p0) * t(la0) ** nn / nn, rho = lat => F / t(lat) ** nn, r0 = rho(la0);
                fx = (lon, lat) => rho(lat) * Math.sin(nn * wrap180(lon - lo0) * D2R);
                fy = (lon, lat) => r0 - rho(lat) * Math.cos(nn * wrap180(lon - lo0) * D2R);
                ix = (x, y) => {
                    const r = Math.sign(nn) * Math.hypot(x, r0 - y), th = Math.atan2(x, r0 - y);
                    if (Math.abs(th) > Math.PI * Math.abs(nn)) return null;     // the gap of the cone
                    return [lo0 + th / nn / D2R, (2 * Math.atan((F / r) ** (1 / nn)) - Math.PI / 2) / D2R];
                };
                // frame = rectangle around the projected box (its edges are curved): sample them
                [X0, X1, Y0, Y1] = [Infinity, -Infinity, Infinity, -Infinity];
                for (let k = 0; k <= 200; k++) {
                    const lon = w + (e - w) * k / 200, lat = s + (n - s) * k / 200;
                    [[lon, s], [lon, n], [w, lat], [e, lat]].forEach(([a, b]) => {
                        const x = fx(a, b), y = fy(a, b);
                        X0 = Math.min(X0, x); X1 = Math.max(X1, x); Y0 = Math.min(Y0, y); Y1 = Math.max(Y1, y);
                    });
                }
            }
            const k = W / (X1 - X0);                // css px per projected unit (same in x and y)
            H = Math.round((Y1 - Y0) * k);
            return { W, H, pw: Math.round(W * SC), ph: Math.round(H * SC),
                     fwd: (lon, lat) => [(fx(lon, lat) - X0) * k, (Y1 - fy(lon, lat)) * k],
                     inv: (px, py) => ix(X0 + (px + 0.5) / SC / k, Y1 - (py + 0.5) / SC / k) };
        }
        function drawLines(ctx, geo, P, sty) {
            ctx.strokeStyle = sty.color || "#3a3a3a";
            ctx.lineWidth = sty.width || 0.8;
            ctx.lineJoin = "round";
            ctx.beginPath();
            const line = cs => {
                let prev = null;
                cs.forEach(([lon, lat]) => {
                    const [x, y] = P.fwd(lon, lat);
                    // a jump of > 180 deg (antimeridian) starts a new piece instead of a line across the map
                    if (prev === null || Math.abs(lon - prev) > 180) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                    prev = lon;
                });
            };
            geo.features.forEach(f => {
                const g = f.geometry;
                if (!g) return;
                if (g.type === "LineString") line(g.coordinates);
                else if (g.type === "MultiLineString" || g.type === "Polygon") g.coordinates.forEach(line);
                else if (g.type === "MultiPolygon") g.coordinates.forEach(p => p.forEach(line));
            });
            ctx.stroke();
        }
        const PROJ_NAME = { mercator: "Mercator", latlon: "Lat/Lon", lambert: "Lambert conformal" };
        async function saveAodPNG(region, kind) {   // region = {name, bounds, origin} or null = current view
            const on = aodOn().filter(A => A.codes);
            if (!on.length) return;
            const SC = 2, TOP = 50, BOT = 62;
            let bb = region && (kind === "lambert" ? region.lambertBounds : region.bounds);
            let W = SNAP.width, origin = region && region.origin;
            if (!bb) {
                const mb = map.getBounds(), mr = map.getContainer().getBoundingClientRect();
                bb = [mb.getWest(), mb.getEast(), mb.getSouth(), mb.getNorth()];
                W = Math.round(mr.width);
                origin = [(bb[2] + bb[3]) / 2, (bb[0] + bb[1]) / 2];
            }
            const P = makeProj(kind, bb, W, SC, origin), H = P.H;
            const geos = await loadBoundaries();
            const cv = document.createElement("canvas");
            cv.width = W * SC; cv.height = (H + TOP + BOT) * SC;
            const ctx = cv.getContext("2d");
            ctx.scale(SC, SC);
            ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, W, H + TOP + BOT);
            ctx.save();
            ctx.beginPath(); ctx.rect(0, TOP, W, H); ctx.clip();       // map area
            ctx.translate(0, TOP);
            ctx.imageSmoothingEnabled = false;
            on.forEach(A => ctx.drawImage(A.paint(P), 0, 0, W, H));    // VIIRS below TEMPO
            let nB = 0;
            geos.forEach((g, k) => { if (g && g.features) { drawLines(ctx, g, P, SNAP.boundaries[k]); nB++; } });
            ctx.restore();
            ctx.strokeStyle = "#333"; ctx.lineWidth = 1; ctx.strokeRect(0.5, TOP + 0.5, W - 1, H - 1);
            // Title
            const when = aodSel === "mean" ? `mean of the hours with data, ${CFG.window[0]} – ${CFG.window[1]} UTC`
                                           : `${slotLabel(aodSel)} (±30 min)`;
            ctx.fillStyle = "#111"; ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
            ctx.font = "bold 16px Helvetica, Arial, sans-serif";
            ctx.fillText(`${on.map(A => A.VC.label).join(" + ")} — ${when}`, 10, 22);
            ctx.font = "12px Helvetica, Arial, sans-serif"; ctx.fillStyle = "#555";
            const projTxt = kind === "lambert" ? `Lambert conformal, origin ${deg(origin[0], "N", "S", 2)} ${deg(origin[1], "E", "W", 2)}`
                                               : PROJ_NAME[kind];
            ctx.fillText(`Cycle ${CFG.cycle}${region ? " · " + region.name : ""} · ${projTxt}`, 10, 40);
            ctx.textAlign = "right";
            ctx.fillText(`${deg(bb[2], "N", "S", 2)} – ${deg(bb[3], "N", "S", 2)}, ` +
                         `${deg(bb[0], "E", "W", 2)} – ${deg(bb[1], "E", "W", 2)}`, W - 10, 40);
            // Colour bar (scale of the first layer shown)
            const S = on[0].VC.scale, nb = S.colors.length, bw = Math.min(W - 120, 44 * nb);
            const x0 = (W - bw) / 2, y0 = TOP + H + 12, sw = bw / nb;
            S.colors.forEach((c, k) => { ctx.fillStyle = c; ctx.fillRect(x0 + k * sw, y0, sw + 0.5, 12); });
            ctx.strokeStyle = "#111"; ctx.strokeRect(x0 + 0.5, y0 + 0.5, bw, 12);
            ctx.fillStyle = "#111"; ctx.font = "11px Helvetica, Arial, sans-serif"; ctx.textAlign = "center";
            S.bounds.slice(1).forEach((v, k) => ctx.fillText(String(v), x0 + (k + 1) * sw, y0 + 26));
            // ctx.textAlign = "left"; ctx.fillText("AOD 550 nm", x0 + bw + 8, y0 + 10);
            ctx.textAlign = "right"; ctx.fillStyle = "#777"; ctx.font = "10px Helvetica, Arial, sans-serif";
            ctx.fillText(nB ? "Boundaries: Natural Earth" : "(boundaries not available)", W - 8, TOP + H + BOT - 6);
            // Download
            const stamp = aodSel === "mean" ? `mean_${CFG.cycleDate.replaceAll("-", "")}`
                                            : slotLabel(aodSel).slice(0, 16).replace(/[-:]/g, "").replace(" ", "_");
            const reg = region ? "_" + region.name.replace(/[^A-Za-z0-9]+/g, "") : "";
            const blob = await new Promise(res => cv.toBlob(res, "image/png"));
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${on.map(A => A.key).join("_")}_aod550${reg}_${kind}_${stamp}.png`;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        }
        // ---- options window (centre of the screen): region + projection ----
        (function snapDialog() {
            const modal = document.getElementById("snap-modal"), opts = document.getElementById("snap-opts");
            const go = document.getElementById("snap-go");
            const regions = [{ name: "Current map view", bounds: null }, ...SNAP.regions];
            const bstr = b => `${deg(b[0], "E", "W", 0)} – ${deg(b[1], "E", "W", 0)}, ${deg(b[2], "N", "S", 0)} – ${deg(b[3], "N", "S", 0)}`;
            opts.innerHTML = `<div class="grp">Region</div>` + regions.map((r, k) =>
                `<label><input type="radio" name="snap-reg" value="${k}" ${k ? "" : "checked"}>${r.name}` +
                `<span class="cnt" data-k="${k}">${r.bounds ? bstr(r.bounds) : "as on screen"}</span></label>`).join("") +
                `<div class="grp">Projection</div>` + Object.entries(PROJ_NAME).map(([k, t]) =>
                `<label><input type="radio" name="snap-proj" value="${k}" ${k === SNAP.projection ? "checked" : ""}>${t}</label>`).join("");
            const close = () => { modal.hidden = true; };
            opts.addEventListener("change", () => {      // Lambert: show its own box per region
                const lam = modal.querySelector("input[name=snap-proj]:checked").value === "lambert";
                regions.forEach((r, k) => { if (r.bounds) opts.querySelector(`.cnt[data-k="${k}"]`).textContent =
                    bstr(lam ? r.lambertBounds : r.bounds); });
            });
            document.getElementById("aod-save").onclick = () => {
                if (!aodOn().some(A => A.codes)) return;
                document.getElementById("snap-sub").textContent =
                    `${aodOn().map(A => A.VC.label).join(" + ")} — ${aodSel === "mean" ? "mean" : slotLabel(aodSel)}`;
                modal.hidden = false;
            };
            document.getElementById("snap-cancel").onclick = close;
            modal.addEventListener("click", e => { if (e.target === modal) close(); });     // click outside
            document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
            go.onclick = async () => {
                const r = regions[+modal.querySelector("input[name=snap-reg]:checked").value];
                const kind = modal.querySelector("input[name=snap-proj]:checked").value;
                go.disabled = true; go.textContent = "Saving…";
                try { await saveAodPNG(r.bounds ? r : null, kind); close(); }
                finally { go.disabled = false; go.textContent = "Save PNG"; }
            };
        })();

        // ======================= REFERENCE OBSERVATIONS (left panel) =======================
        // HMS fires, VIIRS and TEMPO AOD can be shown over any platform. The left-panel
        // checkbox and the layer control (bottom right) act on the same Leaflet layer, so they
        // always agree. Draw order: base map < AOD < boundaries < stations/fires.
        function refToggle(key, layer, label, count, noData) {
            const box = document.getElementById(`ref_${key}`), row = box.closest(".ref-row");
            document.getElementById(`ref_${key}_lbl`).textContent = label;
            document.getElementById(`ref_${key}_cnt`).textContent = count;
            if (!layer) { box.disabled = true; row.classList.add("no-data"); row.title = noData; return; }
            box.addEventListener("change", () => box.checked ? map.addLayer(layer) : map.removeLayer(layer));
            layer.on("add", () => { box.checked = true; });
            layer.on("remove", () => { box.checked = false; });
            layerControl.addOverlay(layer, label);
        }
        const nFires = FIRE_P.fires.lon.length;
        refToggle("hms", nFires ? fireLayer : null, CFG.ref.hms.label, nFires ? `${nFires} locations` : "",
                  "No HMS fire detections for this cycle");
        AOD_KEYS.forEach(k => {
            const A = AODS.find(x => x.key === k);
            refToggle(k, A ? A.layer : null, CFG.ref[k].label, A ? `${A.slots.length} of ${NSLOT} h` : "",
                      `No ${CFG.ref[k].label} for this cycle`);
        });
        // Opacity / PNG row and the time slider follow the AOD checkboxes
        const syncAodControls = () => {
            document.getElementById("aod-opac").classList.toggle("off", !aodOn().length);
            updateSlider();
            updateAodLegend();
        };
        AODS.forEach(A => {
            A.layer.on("add", () => { syncAodControls(); A.render(aodSel); });
            A.layer.on("remove", () => { A.codes = null; hideHover(); syncAodControls(); });
        });
        // Start frame (config reference.aod_display.start): mean, or the first/latest hour with
        // data among the layers shown at start
        const startSlots = AODS.filter(A => A.VC.show).flatMap(A => A.slots).sort((a, b) => a - b);
        aodSel = !startSlots.length || AD.start === "mean" ? "mean"
               : AD.start === "first" ? startSlots[0] : startSlots[startSlots.length - 1];
        syncAodControls();
        AODS.forEach(A => { if (A.VC.show) map.addLayer(A.layer); });
        if (CFG.ref.hms.show && nFires) map.addLayer(fireLayer);   // fires last = drawn on top

        // ======================= CYCLE DATE PICKER =======================
        // config date_navigation.url with {yyyymmdd} (cycle date) and {page} (this page's
        // file name, so every model_type links to its own page). Dates before first_date
        // and in the future are greyed out; on the web server each shown day is also checked
        // with a HEAD request and greyed out when its page doesn't exist.
        (function datePicker() {
            const DN = CFG.dateNav, btn = document.getElementById("date-btn"), pop = document.getElementById("date-pop");
            btn.textContent = `📅 ${CFG.cycleDate} ▾`;
            if (!DN) { btn.disabled = true; btn.textContent = CFG.cycleDate; return; }
            const page = decodeURIComponent(location.pathname.split("/").pop() || "");
            const urlFor = d => DN.url.replace("{yyyymmdd}", d.replaceAll("-", "")).replace("{page}", page);
            const iso = dt => dt.toISOString().slice(0, 10);
            const today = iso(new Date()), first = DN.first_date || "1900-01-01";
            // Pages can only be checked when served over http(s); opened as a local file,
            // every other date stays greyed out.
            const canCheck = location.protocol.startsWith("http");
            const avail = {};                        // date -> true | false | "checking"
            let view = new Date(CFG.cycleDate + "T00:00:00Z");
            view.setUTCDate(1);

            function check(d) {
                if (!canCheck || d in avail) return;
                avail[d] = "checking";
                fetch(urlFor(d), { method: "HEAD", cache: "no-store" })
                    .then(r => { avail[d] = r.ok; })
                    .catch(() => { avail[d] = false; })       // can't confirm -> not selectable
                    .finally(() => { if (!pop.hidden) render(); });
            }
            function render() {
                const y = view.getUTCFullYear(), m = view.getUTCMonth();
                const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate(), lead = new Date(Date.UTC(y, m, 1)).getUTCDay();
                const monthName = view.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
                let h = `<div class="cal-head"><button type="button" data-nav="-1" ${iso(view) <= first.slice(0, 8) + "01" ? "disabled" : ""}>‹</button>` +
                        `<span>${monthName}</span><button type="button" data-nav="1" ${iso(new Date(Date.UTC(y, m + 1, 1))) > today ? "disabled" : ""}>›</button></div>` +
                        `<div class="cal-grid">` + ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(d => `<div class="dow">${d}</div>`).join("") +
                        `<div></div>`.repeat(lead);
                for (let k = 1; k <= days; k++) {
                    const d = iso(new Date(Date.UTC(y, m, k)));
                    let cls = "cal-day", title = "";
                    if (d === CFG.cycleDate) { cls += " current"; title = "This page"; }
                    else if (d < first || d > today) { cls += " off"; title = "Not available"; }
                    else if (!canCheck) { cls += " off"; title = "Only available on the web server"; }
                    else {
                        check(d);
                        if (avail[d] === true) title = "Open this date";
                        else if (avail[d] === "checking") { cls += " off checking"; title = "Checking…"; }
                        else { cls += " off"; title = "No page for this date"; }
                    }
                    if (d === today) cls += " today";
                    h += `<div class="${cls}" data-date="${d}" title="${title}">${k}</div>`;
                }
                h += `</div><div class="cal-note">${canCheck ? "Greyed out: no page for that date." : "Other dates can only be opened on the web server."}</div>`;
                pop.innerHTML = h;
            }
            btn.addEventListener("click", e => { e.stopPropagation(); pop.hidden = !pop.hidden; if (!pop.hidden) render(); });
            pop.addEventListener("click", e => {
                e.stopPropagation();
                const nav = e.target.closest("[data-nav]");
                if (nav && !nav.disabled) { view.setUTCMonth(view.getUTCMonth() + +nav.dataset.nav); render(); return; }
                const day = e.target.closest(".cal-day");
                if (!day || day.classList.contains("off") || day.classList.contains("current")) return;
                window.location.href = urlFor(day.dataset.date);
            });
            document.addEventListener("click", () => { pop.hidden = true; });
        })();

        selectPlatform(activePlatform);
    </script>
</body>
</html>
"""

html = (DASHBOARD_TEMPLATE
        .replace("__PLOTLY__", PLOTLY_CDN)
        .replace("__DARK_BG__", cfg["map"]["themes"]["dark"].get("panel_bg", "#171717"))
        .replace("__LIGHT_BG__", cfg["map"]["themes"]["light"].get("panel_bg", "#ebebeb"))
        .replace("__POP_W__", str(pop["width"])).replace("__POP_H__", str(pop["height"]))
        .replace("__POP_MINW__", str(pop["min_width"])).replace("__POP_MINH__", str(pop["min_height"]))
        .replace("__CFG__", js_literal(page_cfg))
        .replace("__REF__", js_literal(ref_data))
        .replace("__DATA__", js_literal(all_data)))
os.makedirs(os.path.dirname(os.path.abspath(cfg["output_html"])), exist_ok=True)
with open(cfg["output_html"], "w") as f:
    f.write(html)
print(f"{cfg['output_html']}: {os.path.getsize(cfg['output_html']) / 1024**2:.2f} MB  (" + "; ".join(counts) + ")")
