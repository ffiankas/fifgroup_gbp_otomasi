"""
map_coverage_service.py — Service untuk Branch Coverage Mode di Map View.

Mengelompokkan network berdasarkan kesamaan 3 digit pertama store_code,
membuat coverage polygon/circle overlay, dan menghasilkan Folium map HTML.

Fungsi utama:
  normalize_store_code(value)             → Normalisasi store_code dari berbagai format
  get_store_code_prefix(store_code)       → Ambil 3 digit pertama
  normalize_network_type(value)           → Normalisasi jenis network
  get_latest_network_points(run_id)       → Gabungkan MasterLocation + Snapshot
  build_branch_coverage_groups(run_id)    → Grouping coverage per cabang
  calculate_branch_coverage_summary(groups) → Summary agregat
  get_selected_branch_detail(groups, prefix) → Detail 1 cabang
  build_branch_coverage_map(groups, ...)  → Generate Folium map HTML
"""

import logging
import math
import re
from collections import defaultdict

from gbp.models import FetchRun, LocationSnapshot, MasterLocation

log = logging.getLogger("gbp.services.map_coverage_service")


# ══════════════════════════════════════════════════════════════════════
# COLOR PALETTE
# ══════════════════════════════════════════════════════════════════════

BRANCH_COLORS = [
    "#E20820",  # FIFGROUP Red
    "#2563EB",  # Blue
    "#16A34A",  # Green
    "#F59E0B",  # Amber
    "#7C3AED",  # Purple
    "#0891B2",  # Cyan
    "#DB2777",  # Pink
    "#65A30D",  # Lime
    "#EA580C",  # Orange
    "#4F46E5",  # Indigo
    "#0D9488",  # Teal
    "#B91C1C",  # Dark Red
    "#1D4ED8",  # Dark Blue
    "#15803D",  # Dark Green
    "#A16207",  # Dark Amber
    "#6D28D9",  # Dark Purple
]

NETWORK_MARKER_COLORS = {
    "Pos": "#8B5CF6",       # Purple
    "Kios": "#F97316",      # Orange
    "Subkios": "#06B6D4",   # Cyan
}


# ══════════════════════════════════════════════════════════════════════
# HELPER: NORMALISASI
# ══════════════════════════════════════════════════════════════════════

def normalize_store_code(value) -> str:
    """
    Normalisasi store_code dari berbagai format:
    - Convert ke string
    - Strip whitespace
    - Hapus trailing .0 (artefak Excel)
    - Ambil hanya digit
    """
    if value is None:
        return ""
    s = str(value).strip()
    # Hapus trailing .0 dari float Excel
    if s.endswith(".0"):
        s = s[:-2]
    # Ambil hanya karakter digit
    s = re.sub(r"[^\d]", "", s)
    return s


def get_store_code_prefix(store_code) -> str:
    """Return 3 digit pertama dari store_code yang sudah dinormalisasi."""
    normalized = normalize_store_code(store_code)
    if len(normalized) >= 3:
        return normalized[:3]
    return ""


def normalize_network_type(value) -> str:
    """
    Normalisasi jenis network ke format standar.
    Return: Cabang, Pos, Kios, Subkios, atau Unknown
    """
    if not value:
        return "Unknown"
    v = str(value).strip().lower()
    if v in ("cabang", "branch"):
        return "Cabang"
    if v == "pos":
        return "Pos"
    if v in ("subkios", "sub kios", "sub-kios"):
        return "Subkios"
    if v == "kios":
        return "Kios"
    # Partial matches (more lenient)
    if "subkios" in v or "sub kios" in v:
        return "Subkios"
    if "kios" in v:
        return "Kios"
    if "cabang" in v:
        return "Cabang"
    if "pos" in v:
        return "Pos"
    return "Unknown"


# ══════════════════════════════════════════════════════════════════════
# DATA: GABUNGKAN MASTER + SNAPSHOT
# ══════════════════════════════════════════════════════════════════════

def get_latest_network_points(run_id=None):
    """
    Gabungkan MasterLocation (basis utama) dengan koordinat dari LocationSnapshot.

    Returns:
        list of dict, masing-masing berisi:
        {
            "master_id", "store_code", "prefix", "name", "network",
            "area", "latitude", "longitude", "status", "address",
            "has_coordinates", "is_valid_store_code"
        }
    """
    # 1. Ambil run terbaru jika tidak dispesifikasi
    run = None
    if run_id:
        run = FetchRun.objects.filter(id=run_id).first()
    if not run:
        run = FetchRun.objects.order_by("-pk").first()

    # 2. Build lookup dari snapshot
    snap_by_store = {}
    snap_by_biz = {}
    snap_by_loc = {}
    if run:
        snapshots = LocationSnapshot.objects.filter(run=run)
        for s in snapshots:
            if s.store_code:
                snap_by_store[s.store_code] = s
            if s.business_name:
                snap_by_biz[s.business_name] = s
            if s.location_name:
                snap_by_loc[s.location_name] = s

    # 3. Iterasi semua MasterLocation
    masters = MasterLocation.objects.all()
    points = []

    for m in masters:
        sc = normalize_store_code(m.store_code)
        prefix = get_store_code_prefix(m.store_code)
        network = normalize_network_type(m.network)

        # Cari snapshot yang cocok untuk koordinat & status
        snap = None
        if m.store_code and m.store_code in snap_by_store:
            snap = snap_by_store[m.store_code]
        elif sc and sc in snap_by_store:
            snap = snap_by_store.get(sc)
        elif m.location_name and m.location_name in snap_by_loc:
            snap = snap_by_loc[m.location_name]
        elif m.business_name and m.business_name in snap_by_biz:
            snap = snap_by_biz[m.business_name]

        lat = snap.latitude if snap and snap.latitude is not None else None
        lng = snap.longitude if snap and snap.longitude is not None else None
        status = snap.status if snap else (m.verification_status or "Unverified")
        address = snap.address if snap and snap.address else ""

        has_coords = lat is not None and lng is not None
        is_valid_sc = len(prefix) == 3

        points.append({
            "master_id": m.pk,
            "store_code": m.store_code or "",
            "store_code_normalized": sc,
            "prefix": prefix,
            "name": m.network_name or m.business_name or "",
            "network": network,
            "area": m.area or "",
            "latitude": lat,
            "longitude": lng,
            "status": status,
            "address": address,
            "has_coordinates": has_coords,
            "is_valid_store_code": is_valid_sc,
        })

    return points


# ══════════════════════════════════════════════════════════════════════
# COVERAGE GROUPING
# ══════════════════════════════════════════════════════════════════════

def build_branch_coverage_groups(run_id=None):
    """
    Membangun coverage groups berdasarkan kesamaan 3 digit pertama store_code.

    Returns:
        dict {
            "groups": [...],             # list coverage group per cabang
            "unmapped": [...],           # child network tanpa cabang
            "invalid_store_codes": [...], # network dengan store code invalid
            "duplicate_prefixes": [...], # prefix yang punya >1 cabang
            "no_coord_networks": [...],  # network tanpa koordinat
        }
    """
    points = get_latest_network_points(run_id)

    # Pisahkan berdasarkan validitas
    branches = {}       # prefix -> list of branch points
    children = {}       # prefix -> list of child points
    invalid_sc = []     # store code invalid
    no_coord = []       # tanpa koordinat

    for p in points:
        if not p["is_valid_store_code"]:
            invalid_sc.append(p)
            continue

        if not p["has_coordinates"]:
            no_coord.append(p)

        prefix = p["prefix"]
        network = p["network"]

        if network == "Cabang":
            branches.setdefault(prefix, []).append(p)
        elif network in ("Pos", "Kios", "Subkios"):
            children.setdefault(prefix, []).append(p)
        else:
            # Unknown network type, treat as child
            children.setdefault(prefix, []).append(p)

    # Identifikasi duplicate prefixes (prefix dengan >1 cabang)
    duplicate_prefixes = []
    for prefix, branch_list in branches.items():
        if len(branch_list) > 1:
            duplicate_prefixes.append({
                "prefix": prefix,
                "branches": branch_list,
                "count": len(branch_list),
            })

    # Build groups
    groups = []
    color_idx = 0
    all_covered_prefixes = set()

    for prefix in sorted(branches.keys()):
        branch_list = branches[prefix]
        # Gunakan cabang pertama sebagai representative
        branch = branch_list[0]
        is_duplicate = len(branch_list) > 1

        covered = children.get(prefix, [])
        all_covered_prefixes.add(prefix)

        # Hitung summary
        summary = _calc_group_summary(covered)

        # Assign color
        color = BRANCH_COLORS[color_idx % len(BRANCH_COLORS)]
        color_idx += 1

        groups.append({
            "branch": {
                "master_id": branch["master_id"],
                "store_code": branch["store_code"],
                "prefix": prefix,
                "name": branch["name"],
                "area": branch["area"],
                "latitude": branch["latitude"],
                "longitude": branch["longitude"],
                "status": branch["status"],
                "has_coordinates": branch["has_coordinates"],
            },
            "all_branches": branch_list if is_duplicate else [branch],
            "covered_networks": covered,
            "summary": summary,
            "color": color,
            "manual_review": is_duplicate,
        })

    # Unmapped = children dengan prefix yang tidak ada cabangnya
    unmapped = []
    for prefix, child_list in children.items():
        if prefix not in branches:
            unmapped.extend(child_list)

    return {
        "groups": groups,
        "unmapped": unmapped,
        "invalid_store_codes": invalid_sc,
        "duplicate_prefixes": duplicate_prefixes,
        "no_coord_networks": no_coord,
    }


def _calc_group_summary(covered_networks):
    """Hitung summary statistik untuk satu coverage group."""
    total = len(covered_networks)
    pos = sum(1 for n in covered_networks if n["network"] == "Pos")
    kios = sum(1 for n in covered_networks if n["network"] == "Kios")
    subkios = sum(1 for n in covered_networks if n["network"] == "Subkios")
    unknown = sum(1 for n in covered_networks if n["network"] == "Unknown")

    verified = sum(1 for n in covered_networks if n["status"] == "Verified")
    need_verif = sum(1 for n in covered_networks if n["status"] == "Need Verification")
    unverified = sum(1 for n in covered_networks if n["status"] == "Unverified")
    suspended = sum(1 for n in covered_networks if n["status"] == "Suspended")
    duplicate = sum(1 for n in covered_networks if n["status"] == "Duplicate")

    verification_rate = round(verified / total * 100, 1) if total > 0 else 0.0

    return {
        "total": total,
        "pos": pos,
        "kios": kios,
        "subkios": subkios,
        "unknown": unknown,
        "verified": verified,
        "need_verification": need_verif,
        "unverified": unverified,
        "suspended": suspended,
        "duplicate": duplicate,
        "verification_rate": verification_rate,
    }


def calculate_branch_coverage_summary(result):
    """
    Hitung summary agregat dari semua coverage groups.

    Args:
        result: dict dari build_branch_coverage_groups()

    Returns:
        dict summary keseluruhan
    """
    groups = result.get("groups", [])

    total_branches = len(groups)
    total_pos = 0
    total_kios = 0
    total_subkios = 0
    total_covered = 0
    max_coverage_branch = None
    max_coverage_count = 0
    max_need_verif_branch = None
    max_need_verif_count = 0
    max_suspended_branch = None
    max_suspended_count = 0

    for g in groups:
        s = g["summary"]
        total_pos += s["pos"]
        total_kios += s["kios"]
        total_subkios += s["subkios"]
        total_covered += s["total"]

        if s["total"] > max_coverage_count:
            max_coverage_count = s["total"]
            max_coverage_branch = g["branch"]["name"]

        if s["need_verification"] > max_need_verif_count:
            max_need_verif_count = s["need_verification"]
            max_need_verif_branch = g["branch"]["name"]

        if s["suspended"] > max_suspended_count:
            max_suspended_count = s["suspended"]
            max_suspended_branch = g["branch"]["name"]

    avg_coverage = round(total_covered / total_branches, 1) if total_branches > 0 else 0

    return {
        "total_branches": total_branches,
        "total_pos": total_pos,
        "total_kios": total_kios,
        "total_subkios": total_subkios,
        "total_covered": total_covered,
        "avg_coverage": avg_coverage,
        "max_coverage_branch": max_coverage_branch or "—",
        "max_coverage_count": max_coverage_count,
        "max_need_verif_branch": max_need_verif_branch or "—",
        "max_need_verif_count": max_need_verif_count,
        "max_suspended_branch": max_suspended_branch or "—",
        "max_suspended_count": max_suspended_count,
        "unmapped_count": len(result.get("unmapped", [])),
        "invalid_count": len(result.get("invalid_store_codes", [])),
        "duplicate_prefix_count": len(result.get("duplicate_prefixes", [])),
        "no_coord_count": len(result.get("no_coord_networks", [])),
    }


def get_selected_branch_detail(result, prefix):
    """
    Ambil detail coverage group untuk 1 prefix cabang.

    Args:
        result: dict dari build_branch_coverage_groups()
        prefix: string 3 digit prefix

    Returns:
        dict group atau None
    """
    if not prefix:
        return None
    for g in result.get("groups", []):
        if g["branch"]["prefix"] == prefix:
            return g
    return None


# ══════════════════════════════════════════════════════════════════════
# CONVEX HULL (Graham Scan — no external deps)
# ══════════════════════════════════════════════════════════════════════

def _cross(o, a, b):
    """Cross product of vectors OA and OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(points_2d):
    """
    Compute convex hull using Andrew's monotone chain algorithm.
    Input: list of (x, y) tuples.
    Returns: list of (x, y) tuples forming the convex hull in CCW order.
    """
    pts = sorted(set(points_2d))
    if len(pts) <= 1:
        return pts

    # Build lower hull
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


# ══════════════════════════════════════════════════════════════════════
# CIRCLE BUFFER (approximate circle as polygon)
# ══════════════════════════════════════════════════════════════════════

def _circle_polygon(lat, lng, radius_km=3, num_points=36):
    """
    Generate a circle polygon around a point.
    Returns list of [lat, lng] pairs.
    """
    coords = []
    for i in range(num_points):
        angle = math.radians(360 / num_points * i)
        # Approximate: 1 degree lat ≈ 111km, 1 degree lng ≈ 111km * cos(lat)
        d_lat = (radius_km / 111.0) * math.cos(angle)
        d_lng = (radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))) * math.sin(angle)
        coords.append([lat + d_lat, lng + d_lng])
    coords.append(coords[0])  # Close the polygon
    return coords


# ══════════════════════════════════════════════════════════════════════
# MAP RENDERING
# ══════════════════════════════════════════════════════════════════════

def build_branch_coverage_map(result, selected_branch_prefix=None):
    """
    Generate Folium map HTML untuk Branch Coverage Mode.

    Args:
        result: dict dari build_branch_coverage_groups()
        selected_branch_prefix: prefix cabang yang dipilih (optional)

    Returns:
        string HTML map, atau "" jika tidak ada data
    """
    import folium

    groups = result.get("groups", [])
    if not groups:
        return ""

    # Kumpulkan semua titik valid untuk centering
    all_coords = []
    for g in groups:
        b = g["branch"]
        if b["has_coordinates"]:
            all_coords.append([b["latitude"], b["longitude"]])
        for c in g.get("covered_networks", []):
            if c.get("has_coordinates"):
                all_coords.append([c["latitude"], c["longitude"]])

    if not all_coords:
        return ""

    # Center map
    center_lat = sum(c[0] for c in all_coords) / len(all_coords)
    center_lng = sum(c[1] for c in all_coords) / len(all_coords)

    m = folium.Map(location=[center_lat, center_lng], zoom_start=6, tiles="CartoDB positron")

    # Render setiap coverage group
    for g in groups:
        branch = g["branch"]
        color = g["color"]
        covered = g["covered_networks"]
        prefix = branch["prefix"]
        is_selected = (selected_branch_prefix == prefix)

        # Kumpulkan semua titik valid dalam group (branch + children)
        group_coords = []
        if branch["has_coordinates"]:
            group_coords.append((branch["latitude"], branch["longitude"]))

        for c in covered:
            if c["has_coordinates"]:
                group_coords.append((c["latitude"], c["longitude"]))

        # Coverage area overlay
        if len(group_coords) >= 3:
            # Convex Hull Polygon
            hull = _convex_hull(group_coords)
            if len(hull) >= 3:
                hull_latlng = [[p[0], p[1]] for p in hull]
                folium.Polygon(
                    locations=hull_latlng,
                    color=color,
                    weight=2,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.18,
                    opacity=0.5,
                    tooltip=f"Coverage: {branch['name']} ({prefix})",
                ).add_to(m)
        elif len(group_coords) >= 1:
            # Circle buffer
            center_pt = group_coords[0]
            circle_coords = _circle_polygon(center_pt[0], center_pt[1], radius_km=3)
            folium.Polygon(
                locations=circle_coords,
                color=color,
                weight=2,
                fill=True,
                fill_color=color,
                fill_opacity=0.18,
                opacity=0.5,
                tooltip=f"Coverage: {branch['name']} ({prefix})",
            ).add_to(m)

        # Branch marker (selalu tampil)
        if branch["has_coordinates"]:
            summary = g["summary"]
            review_badge = ' <span style="background:#DC2626;color:white;padding:1px 6px;border-radius:4px;font-size:10px;">Manual Review</span>' if g.get("manual_review") else ""

            popup_html = f"""
            <div style="font-family:Inter,sans-serif;min-width:280px;font-size:13px;padding:6px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                    <div style="width:12px;height:12px;border-radius:50%;background:{color};flex-shrink:0;"></div>
                    <b style="font-size:15px;color:#0F172A">{branch['name']}</b>
                </div>
                <div style="color:#64748B;font-size:12px;margin-bottom:4px">
                    Store Code: <span style="font-family:monospace;color:#0F172A">{branch['store_code']}</span>
                    | Prefix: <span style="font-family:monospace;font-weight:600;color:{color}">{prefix}</span>
                    {review_badge}
                </div>
                <div style="color:#64748B;font-size:12px;margin-bottom:8px">Area: {branch['area'] or '—'}</div>
                <hr style="margin:8px 0;border-color:#E2E8F0">
                <div style="font-weight:600;color:#0F172A;margin-bottom:6px">Coverage Summary</div>
                <table style="width:100%;font-size:12px;border-collapse:collapse">
                    <tr><td style="padding:2px 0;color:#64748B">Pos</td><td style="text-align:right;font-weight:600">{summary['pos']}</td></tr>
                    <tr><td style="padding:2px 0;color:#64748B">Kios</td><td style="text-align:right;font-weight:600">{summary['kios']}</td></tr>
                    <tr><td style="padding:2px 0;color:#64748B">Subkios</td><td style="text-align:right;font-weight:600">{summary['subkios']}</td></tr>
                    <tr style="border-top:1px solid #E2E8F0"><td style="padding:4px 0;font-weight:600;color:#0F172A">Total</td><td style="text-align:right;font-weight:700;color:#0F172A">{summary['total']}</td></tr>
                </table>
                <hr style="margin:8px 0;border-color:#E2E8F0">
                <div style="font-size:11px;color:#64748B">
                    ✅ {summary['verified']} Verified
                    · ⚠️ {summary['need_verification']} Need Verif
                    · 🚫 {summary['suspended']} Suspended
                </div>
                <div style="margin-top:4px;font-size:11px;color:{color};font-weight:600">
                    Verification Rate: {summary['verification_rate']}%
                </div>
            </div>
            """

            folium.Marker(
                location=[branch["latitude"], branch["longitude"]],
                popup=folium.Popup(popup_html, max_width=340),
                tooltip=f"🏢 {branch['name']} ({prefix}) — {summary['total']} network",
                icon=folium.Icon(
                    color="red" if color == "#E20820" else "blue",
                    icon="building",
                    prefix="fa",
                ),
            ).add_to(m)

        # Child markers — hanya tampil jika cabang dipilih
        if is_selected:
            for c in covered:
                if not c["has_coordinates"]:
                    continue

                net = c["network"]
                net_color = NETWORK_MARKER_COLORS.get(net, "#94A3B8")

                # Status badge color
                status_color_map = {
                    "Verified": "#16A34A",
                    "Duplicate": "#F59E0B",
                    "Suspended": "#DC2626",
                    "Need Verification": "#94A3B8",
                    "Unverified": "#64748B",
                }
                status_color = status_color_map.get(c["status"], "#94A3B8")

                child_popup = f"""
                <div style="font-family:Inter,sans-serif;min-width:240px;font-size:13px;padding:4px">
                    <b style="font-size:14px;color:#0F172A">{c['name'] or '—'}</b><br>
                    <span style="color:#64748B;font-size:12px">Store Code:
                        <span style="font-family:monospace">{c['store_code']}</span>
                    </span>
                    <hr style="margin:6px 0;border-color:#E2E8F0">
                    <div style="display:flex;gap:6px;margin-bottom:6px">
                        <span style="background:{net_color};color:white;padding:2px 8px;border-radius:9999px;font-size:11px">{net}</span>
                        <span style="background:{status_color};color:white;padding:2px 8px;border-radius:9999px;font-size:11px">{c['status']}</span>
                    </div>
                    <div style="font-size:12px;color:#64748B">📍 {c['address'] or '—'}</div>
                    <div style="font-size:11px;color:#94A3B8;margin-top:4px">
                        Cabang: {branch['name']} ({prefix})
                    </div>
                </div>
                """

                # Marker radius/style berdasarkan jenis
                radius = 6 if net == "Subkios" else 7
                folium.CircleMarker(
                    location=[c["latitude"], c["longitude"]],
                    radius=radius,
                    color=net_color,
                    fill=True,
                    fill_color=net_color,
                    fill_opacity=0.85,
                    popup=folium.Popup(child_popup, max_width=300),
                    tooltip=f"{c['name']} — {net} ({c['status']})",
                ).add_to(m)

    # Legend
    legend_items = ""
    for g in groups[:12]:  # Max 12 in legend
        legend_items += f'<div style="margin-bottom:3px;display:flex;align-items:center;gap:6px"><span style="width:10px;height:10px;border-radius:50%;background:{g["color"]};flex-shrink:0;box-shadow:0 0 0 2px rgba(255,255,255,0.6)"></span><span style="font-size:12px">{g["branch"]["name"]} ({g["branch"]["prefix"]})</span></div>'

    if len(groups) > 12:
        legend_items += f'<div style="font-size:11px;color:#94A3B8;margin-top:4px">+{len(groups) - 12} cabang lainnya</div>'

    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                background:white;padding:14px 18px;border-radius:12px;
                box-shadow:0 4px 16px rgba(0,0,0,0.12);font-family:Inter,sans-serif;
                max-height:300px;overflow-y:auto;max-width:260px">
        <b style="display:block;margin-bottom:8px;color:#0F172A;font-size:13px">Coverage Cabang</b>
        {legend_items}
        <hr style="margin:8px 0;border-color:#E2E8F0">
        <div style="font-size:10px;color:#94A3B8;line-height:1.4">
            Area coverage merupakan estimasi visual berdasarkan titik network,
            bukan batas wilayah administratif resmi.
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m._repr_html_()
