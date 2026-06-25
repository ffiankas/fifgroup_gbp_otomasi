"""
views.py — Django views untuk GBP Monitor.
Semua business logic didelegasikan ke services layer.

Views:
  OverviewView              → GET /
  DataTableView             → GET /data/
  MapView                   → GET /map/
  UpdateStatusView          → GET + POST /update/
  LocationDetailView        → GET /location/<int:pk>/
  DownloadReportView        → GET /download/
  ExportCSVView             → GET /export/csv/
  ExportExcelView           → GET /export/excel/
  DownloadReconciliationView→ GET /reconciliation/<job_id>/download/
  DownloadReconDetailView   → GET /download/recon/<job_id>/
  TriggerFetchView          → POST /api/fetch-run/
  TrendDataView             → GET /api/trend/
"""

import json
import logging
from datetime import datetime

import pandas as pd
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from gbp.forms import DataTableFilterForm, UpdateStatusForm
from gbp.models import FetchRun, LocationSnapshot, MasterLocation, ReconciliationJob, ReconciliationResult
from gbp.services import export_service, history_service, reconciliation_service, dashboard_service
from gbp.utils import ALL_STATUSES, PAGE_SIZE, STATUS_META, get_status_meta

log = logging.getLogger("gbp.views")


# ── Helper ─────────────────────────────────────────────────────────────

def _get_run_context(request: HttpRequest) -> dict:
    """Ambil context umum: semua runs, run yang dipilih, run_id dari query param."""
    all_runs = history_service.get_all_runs()
    run_id = request.GET.get("run_id") or (all_runs[0]["run_id"] if all_runs else None)

    try:
        run_id = int(run_id) if run_id else None
    except (ValueError, TypeError):
        run_id = all_runs[0]["run_id"] if all_runs else None

    sel_run = history_service.get_run_by_id(run_id) if run_id else None

    return {
        "all_runs": all_runs,
        "sel_run": sel_run,
        "sel_run_id": run_id,
        "status_meta": STATUS_META,
        "all_statuses": ALL_STATUSES,
    }


# ══════════════════════════════════════════════════════════════════════
# PAGE 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════

class OverviewView(View):
    template_name = "gbp/overview.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        ctx = _get_run_context(request)

        if not ctx["all_runs"]:
            return render(request, self.template_name, {**ctx, "no_data": True})

        run_id = ctx["sel_run_id"]
        
        summary = dashboard_service.get_overview_summary(run_id)
        verified_growth = dashboard_service.get_verified_growth_timeseries(days=30)
        top_areas = dashboard_service.get_top_areas(run_id, limit=10)
        bottom_areas = dashboard_service.get_bottom_areas(run_id, limit=10)
        status_by_network = dashboard_service.get_status_by_network_type(run_id)
        attention_summary = dashboard_service.get_attention_status_summary(run_id)
        
        # Gauge chart data — progress verifikasi
        gauge_total = summary.get("total", 0)
        gauge_verified = summary.get("verified", 0)
        gauge_pct = round((gauge_verified / gauge_total * 100) if gauge_total else 0, 1)
        gauge_unverified = max(0, gauge_total - gauge_verified)

        ctx.update({
            "summary": summary,
            "verified_growth_json": json.dumps(verified_growth),
            "top_areas": top_areas,
            "bottom_areas": bottom_areas,
            "status_by_network": status_by_network,
            "status_by_network_json": json.dumps(status_by_network),
            "attention_summary": attention_summary,
            "gauge_total": gauge_total,
            "gauge_verified": gauge_verified,
            "gauge_unverified": gauge_unverified,
            "gauge_pct": gauge_pct,
        })

        return render(request, self.template_name, ctx)


# ══════════════════════════════════════════════════════════════════════
# PAGE 2 — DATA TABLE
# ══════════════════════════════════════════════════════════════════════

class DataTableView(View):
    template_name = "gbp/data_table.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        ctx = _get_run_context(request)

        if not ctx["all_runs"]:
            return render(request, self.template_name, {**ctx, "no_data": True})

        run_id = ctx["sel_run_id"]
        form = DataTableFilterForm(request.GET or None)

        statuses = ALL_STATUSES
        search = ""
        sort_col = "business_name"
        sort_order = "asc"
        page_num = 1

        if form.is_valid():
            statuses = form.cleaned_data["statuses"] or ALL_STATUSES
            search = form.cleaned_data["search"] or ""
            sort_col = form.cleaned_data["sort"] or "business_name"
            sort_order = form.cleaned_data["order"] or "asc"
            page_num = form.cleaned_data["page"] or 1

        snapshots = history_service.get_snapshots(
            run_id=run_id,
            status_filter=list(statuses),
            search=search or None,
        )

        reverse = sort_order == "desc"
        snapshots.sort(
            key=lambda x: str(x.get(sort_col, "") or "").lower(),
            reverse=reverse,
        )

        paginator = Paginator(snapshots, PAGE_SIZE)
        page_obj = paginator.get_page(page_num)

        ctx.update({
            "form": form,
            "page_obj": page_obj,
            "paginator": paginator,
            "total_count": len(snapshots),
            "search": search,
            "statuses": statuses,
            "sort_col": sort_col,
            "sort_order": sort_order,
            "get_status_meta": get_status_meta,
        })
        return render(request, self.template_name, ctx)


# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — MAP VIEW
# ══════════════════════════════════════════════════════════════════════

class MapView(View):
    template_name = "gbp/map_view.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        ctx = _get_run_context(request)

        if not ctx["all_runs"]:
            return render(request, self.template_name, {**ctx, "no_data": True})

        run_id = ctx["sel_run_id"]
        # Ambil mode peta: status, network, atau coverage
        selected_mode = request.GET.get("mode", "status")

        # ══════════════════════════════════════════════════════════
        # MODE: BRANCH COVERAGE
        # ══════════════════════════════════════════════════════════
        if selected_mode == "coverage":
            return self._handle_coverage_mode(request, ctx, run_id)

        # ══════════════════════════════════════════════════════════
        # MODE: STATUS / NETWORK (existing — tidak diubah)
        # ══════════════════════════════════════════════════════════

        # Logika Filter Eksklusif:
        # Jika mode status, terapkan filter status saja (semua network muncul)
        # Jika mode network, terapkan filter network saja (semua status muncul)
        ALL_NETWORKS = ["Cabang", "Pos", "Kios/Subkios", "Lainnya"]
        if selected_mode == "status":
            statuses = request.GET.getlist("status") or ALL_STATUSES
            networks = ALL_NETWORKS
        else:
            statuses = ALL_STATUSES
            networks = request.GET.getlist("network") or ALL_NETWORKS

        snapshots = history_service.get_snapshots(run_id=run_id, status_filter=statuses)

        with_coords = [
            s for s in snapshots
            if s.get("latitude") is not None and s.get("longitude") is not None
        ]
        
        # -- Persiapkan Data Network dari Master Data --
        # Kita pasang tipe network sekarang karena dipakai untuk filter & peta
        store_codes = [s["store_code"] for s in with_coords if s.get("store_code")]
        master_qs = MasterLocation.objects.filter(store_code__in=store_codes).values_list("store_code", "network")
        
        def normalize_network(net_type):
            if not net_type: return "Lainnya"
            nl = net_type.lower()
            if "cabang" in nl: return "Cabang"
            if "pos" in nl: return "Pos"
            if "kios" in nl or "subkios" in nl: return "Kios/Subkios"
            return "Lainnya"
            
        network_map = {sc: normalize_network(net) for sc, net in master_qs}

        for s in with_coords:
            s["network_type"] = network_map.get(s["store_code"], "Lainnya")

        # Terapkan filter network
        with_coords = [s for s in with_coords if s["network_type"] in networks]

        without_coords = len(snapshots) - len(with_coords)

        # Koordinat bermasalah
        coord_issues = [
            s for s in snapshots
            if s.get("coord_status") and s.get("coord_status") != "OK"
        ]

        map_html = ""
        map_network_html = ""
        if with_coords:
            import folium

            # Center map
            lats = [s["latitude"] for s in with_coords]
            lngs = [s["longitude"] for s in with_coords]
            center = [sum(lats) / len(lats), sum(lngs) / len(lngs)]

            # ==========================================
            # PETA 1: BERDASARKAN STATUS VERIFIKASI
            # ==========================================
            m = folium.Map(location=center, zoom_start=6, tiles="CartoDB positron")

            STATUS_COLORS = {
                "Verified": "#22c55e",
                "Duplicate": "#f59e0b",
                "Suspended": "#ef4444",
                "Need Verification": "#94a3b8",
            }

            for s in with_coords:
                color = STATUS_COLORS.get(s["status"], "#94a3b8")
                popup_html = f"""
                <div style="font-family:Inter,sans-serif;min-width:240px;font-size:13px;padding:4px">
                    <b style="font-size:14px">{s['business_name'] or '—'}</b><br>
                    <span style="color:#666;font-size:12px">{s['store_code'] or '—'}</span>
                    <hr style="margin:6px 0;border-color:#eee">
                    <div>📍 {s['address'] or '—'}</div>
                    <div style="color:#555;font-size:11px">🌐 {s.get('latitude', ''):.6f}, {s.get('longitude', ''):.6f}</div>
                    <hr style="margin:6px 0;border-color:#eee">
                    <span style="background:{color};color:white;padding:2px 8px;border-radius:9999px;font-size:11px">{s['status']}</span>
                </div>
                """
                folium.CircleMarker(
                    location=[s["latitude"], s["longitude"]],
                    radius=7,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.75,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{s['business_name'] or s['store_code']} — {s['status']}",
                ).add_to(m)

            legend_html = """
            <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                        background:white;padding:14px 18px;border-radius:12px;
                        box-shadow:0 4px 16px rgba(0,0,0,0.12);font-family:Inter,sans-serif;font-size:13px">
                <b style="display:block;margin-bottom:8px;color:#1e293b">Status Verifikasi</b>
                <div style="margin-bottom:4px"><span style="color:#22c55e;font-size:18px">●</span> <span>Verified</span></div>
                <div style="margin-bottom:4px"><span style="color:#f59e0b;font-size:18px">●</span> <span>Duplicate</span></div>
                <div style="margin-bottom:4px"><span style="color:#ef4444;font-size:18px">●</span> <span>Suspended</span></div>
                <div><span style="color:#94a3b8;font-size:18px">●</span> <span>Need Verification</span></div>
            </div>
            """
            m.get_root().html.add_child(folium.Element(legend_html))
            map_html = m._repr_html_()

            # ==========================================
            # PETA 2: BERDASARKAN JENIS NETWORK
            # ==========================================
            m_network = folium.Map(location=center, zoom_start=6, tiles="CartoDB positron")

            def get_network_color(net_type):
                if net_type == "Cabang": return "#3b82f6" # Biru
                if net_type == "Pos": return "#8b5cf6" # Ungu
                if net_type == "Kios/Subkios": return "#f97316" # Jingga
                return "#94a3b8" # Abu-abu (Unknown/Lainnya)

            for s in with_coords:
                net_type = s["network_type"]
                net_color = get_network_color(net_type)
                popup_html = f"""
                <div style="font-family:Inter,sans-serif;min-width:240px;font-size:13px;padding:4px">
                    <b style="font-size:14px">{s['business_name'] or '—'}</b><br>
                    <span style="color:#666;font-size:12px">{s['store_code'] or '—'}</span>
                    <hr style="margin:6px 0;border-color:#eee">
                    <div>📍 {s['address'] or '—'}</div>
                    <div style="color:#555;font-size:11px">🌐 {s.get('latitude', ''):.6f}, {s.get('longitude', ''):.6f}</div>
                    <hr style="margin:6px 0;border-color:#eee">
                    <span style="background:{net_color};color:white;padding:2px 8px;border-radius:9999px;font-size:11px">{net_type}</span>
                </div>
                """
                folium.CircleMarker(
                    location=[s["latitude"], s["longitude"]],
                    radius=7,
                    color=net_color,
                    fill=True,
                    fill_color=net_color,
                    fill_opacity=0.75,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"{s['business_name'] or s['store_code']} — {net_type}",
                ).add_to(m_network)

            legend_network_html = """
            <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                        background:white;padding:14px 18px;border-radius:12px;
                        box-shadow:0 4px 16px rgba(0,0,0,0.12);font-family:Inter,sans-serif;font-size:13px">
                <b style="display:block;margin-bottom:8px;color:#1e293b">Jenis Network</b>
                <div style="margin-bottom:4px"><span style="color:#3b82f6;font-size:18px">●</span> <span>Cabang</span></div>
                <div style="margin-bottom:4px"><span style="color:#8b5cf6;font-size:18px">●</span> <span>Pos</span></div>
                <div style="margin-bottom:4px"><span style="color:#f97316;font-size:18px">●</span> <span>Kios / Subkios</span></div>
                <div><span style="color:#94a3b8;font-size:18px">●</span> <span>Lainnya / Unknown</span></div>
            </div>
            """
            m_network.get_root().html.add_child(folium.Element(legend_network_html))
            map_network_html = m_network._repr_html_()

        ctx.update({
            "map_html": map_html,
            "map_network_html": map_network_html,
            "with_coords_count": len(with_coords),
            "without_coords_count": without_coords,
            "total_count": len(snapshots),
            "selected_statuses": statuses,
            "selected_networks": networks,
            "selected_mode": selected_mode,
            "network_filters": [
                ("Cabang", "#3b82f6", "text-blue-600"),
                ("Pos", "#8b5cf6", "text-purple-600"),
                ("Kios/Subkios", "#f97316", "text-orange-600"),
                ("Lainnya", "#94a3b8", "text-slate-600"),
            ],
            "coord_issues": coord_issues,
        })
        return render(request, self.template_name, ctx)

    # ── Coverage Mode Handler ──────────────────────────────────────
    def _handle_coverage_mode(self, request, ctx, run_id):
        """Handle Branch Coverage Mode: grouping, map, summary, detail."""
        from gbp.services import map_coverage_service as cov_svc

        branch_prefix = request.GET.get("branch_prefix", "").strip()

        try:
            result = cov_svc.build_branch_coverage_groups(run_id)
            coverage_map_html = cov_svc.build_branch_coverage_map(result, selected_branch_prefix=branch_prefix or None)
            coverage_summary = cov_svc.calculate_branch_coverage_summary(result)

            # Build branch list for dropdown
            branch_list = []
            for g in result.get("groups", []):
                b = g["branch"]
                branch_list.append({
                    "prefix": b["prefix"],
                    "name": b["name"],
                    "area": b["area"],
                    "total_covered": g["summary"]["total"],
                    "color": g["color"],
                })

            # Selected branch detail
            selected_branch_detail = None
            if branch_prefix:
                selected_branch_detail = cov_svc.get_selected_branch_detail(result, branch_prefix)

            # Warnings
            coverage_warnings = {
                "duplicate_prefixes": result.get("duplicate_prefixes", []),
                "unmapped": result.get("unmapped", []),
                "invalid_store_codes": result.get("invalid_store_codes", []),
                "no_coord_networks": result.get("no_coord_networks", []),
                "has_warnings": bool(
                    result.get("duplicate_prefixes")
                    or result.get("unmapped")
                    or result.get("invalid_store_codes")
                    or result.get("no_coord_networks")
                ),
            }

        except Exception as exc:
            log.exception("Error building branch coverage data")
            coverage_map_html = ""
            coverage_summary = {}
            branch_list = []
            selected_branch_detail = None
            coverage_warnings = {"has_warnings": False}
            messages.error(request, f"Gagal membangun data coverage: {exc}")

        ctx.update({
            "selected_mode": "coverage",
            "coverage_map_html": coverage_map_html,
            "coverage_summary": coverage_summary,
            "branch_list": branch_list,
            "selected_branch_prefix": branch_prefix,
            "selected_branch_detail": selected_branch_detail,
            "coverage_warnings": coverage_warnings,
        })
        return render(request, self.template_name, ctx)


# ══════════════════════════════════════════════════════════════════════
# PAGE 4 — UPDATE STATUS VERIFIKASI
# ══════════════════════════════════════════════════════════════════════

class UpdateStatusView(View):
    template_name = "gbp/update_status.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        form = UpdateStatusForm()
        return render(request, self.template_name, {
            "form": form,
            "status_meta": STATUS_META,
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        form = UpdateStatusForm(request.POST, request.FILES)
        context = {"form": form, "status_meta": STATUS_META}

        if not form.is_valid():
            return render(request, self.template_name, context)

        try:
            # 1. Ambil data dari GBP API
            from gbp.services.gbp_api import fetch_records
            account_id = form.cleaned_data["account_id"].strip() or None
            api_records = fetch_records(account_id=account_id)

            if not api_records:
                messages.error(request, "Data API kosong. Tidak ada baris yang bisa dibandingkan.")
                return render(request, self.template_name, context)

            api_df = pd.DataFrame(api_records)

            # 2. Baca master data
            source_type = form.cleaned_data["source_type"]
            master_source_label = ""

            if source_type == UpdateStatusForm.SOURCE_CSV:
                master_file = form.cleaned_data.get("master_file")
                master_path = form.cleaned_data.get("master_path", "").strip()

                if master_file:
                    master_df = pd.read_csv(master_file, dtype=str, keep_default_na=False)
                    master_source_label = f"Upload: {master_file.name}"
                else:
                    master_df = pd.read_csv(master_path, dtype=str, keep_default_na=False)
                    master_source_label = master_path
            else:
                master_path = form.cleaned_data.get("master_path", "").strip()
                sqlite_table = form.cleaned_data.get("sqlite_table", "kios").strip()
                import sqlite3
                with sqlite3.connect(master_path) as conn:
                    master_df = pd.read_sql_query(f'SELECT * FROM "{sqlite_table}"', conn)
                master_source_label = f"{master_path} :: {sqlite_table}"

            if master_df.empty:
                messages.warning(request, "Master data kosong, tidak ada yang bisa diupdate.")
                return render(request, self.template_name, context)

            # 3. Rekonsiliasi
            master_status_col = reconciliation_service.detect_status_column(list(master_df.columns))
            api_status_col = reconciliation_service.detect_status_column(list(api_df.columns))

            updated_master_df, comparison_df, summary = reconciliation_service.compare_master_to_api(
                master_df, api_df,
                master_status_col=master_status_col,
                api_status_col=api_status_col,
            )

            comparison_rows = comparison_df.to_dict("records") if not comparison_df.empty else []

            # 4. Simpan ReconciliationJob + Results ke Supabase
            job = reconciliation_service.save_reconciliation_job(
                summary=summary,
                source_type=source_type,
                source_label=master_source_label,
                total_master=len(master_df),
                total_api=len(api_df),
            )
            reconciliation_service.save_reconciliation_results(job, comparison_rows)

            # 5. Update MasterLocation di Supabase jika ada di DB
            reconciliation_service.update_master_statuses(comparison_rows)

            # 6. Simpan ke disk jika diminta
            save_result_msg = None
            if form.cleaned_data.get("save_to_disk"):
                master_path_disk = form.cleaned_data.get("master_path", "").strip()
                if master_path_disk and source_type == UpdateStatusForm.SOURCE_CSV:
                    updated_master_df.to_csv(master_path_disk, index=False, encoding="utf-8")
                    save_result_msg = "✅ CSV master berhasil diperbarui."

            # 7. Data untuk template
            show_cols = [
                "match_status", "match_rule", "identifier_value",
                "old_status", "new_status", "status_changed", "change_note",
            ]
            available_cols = [c for c in show_cols if c in comparison_df.columns]
            comparison_rows_display = comparison_df[available_cols].to_dict("records") if not comparison_df.empty else []

            changed_rows = reconciliation_service.generate_changed_networks(comparison_rows)

            context.update({
                "result": True,
                "job": job,
                "summary": summary,
                "comparison_rows": comparison_rows_display,
                "changed_rows": changed_rows,
                "master_source_label": master_source_label,
                "master_row_count": len(master_df),
                "api_row_count": len(api_df),
                "save_result_msg": save_result_msg,
            })

            if summary.get("updated", 0) > 0:
                messages.success(
                    request,
                    f"✅ Rekonsiliasi selesai. {summary['updated']} status berubah, "
                    f"{summary['matched'] - summary['updated']} tidak berubah."
                )
            else:
                messages.info(request, "ℹ️ Rekonsiliasi selesai. Tidak ada status yang berubah.")

        except Exception as exc:
            log.exception("Error saat update status verifikasi")
            messages.error(request, f"Gagal menjalankan update status: {exc}")

        return render(request, self.template_name, context)


# ══════════════════════════════════════════════════════════════════════
# PAGE 5 — LOCATION DETAIL
# ══════════════════════════════════════════════════════════════════════

class LocationDetailView(View):
    template_name = "gbp/location_detail.html"

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        snapshot = get_object_or_404(LocationSnapshot, pk=pk)
        ctx = _get_run_context(request)
        ctx.update({
            "snapshot": snapshot,
            "status_meta": get_status_meta(snapshot.status),
        })
        return render(request, self.template_name, ctx)


# ══════════════════════════════════════════════════════════════════════
# EXPORT — CSV & EXCEL
# ══════════════════════════════════════════════════════════════════════

class ExportCSVView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        run_id = request.GET.get("run_id")
        statuses = request.GET.getlist("status") or ALL_STATUSES
        search = request.GET.get("search", "") or None

        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            run_id = history_service.get_latest_run_id()
            if not run_id:
                return HttpResponse("Tidak ada data.", status=404)

        snapshots = history_service.get_snapshots(run_id=run_id, status_filter=statuses, search=search)
        df = export_service.snapshots_to_dataframe(snapshots)
        csv_bytes = export_service.to_csv_bytes(df)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="gbp_export_{ts}.csv"'
        return response


class ExportExcelView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        run_id = request.GET.get("run_id")
        statuses = request.GET.getlist("status") or ALL_STATUSES
        search = request.GET.get("search", "") or None

        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            run_id = history_service.get_latest_run_id()
            if not run_id:
                return HttpResponse("Tidak ada data.", status=404)

        snapshots = history_service.get_snapshots(run_id=run_id, status_filter=statuses, search=search)
        df = export_service.snapshots_to_dataframe(snapshots)
        xlsx_bytes = export_service.to_excel_bytes(df)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        response = HttpResponse(
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="gbp_export_{ts}.xlsx"'
        return response


class DownloadReconciliationView(View):
    """Download hasil rekonsiliasi dari database berdasarkan job_id."""

    def get(self, request: HttpRequest, job_id: int) -> HttpResponse:
        job = get_object_or_404(ReconciliationJob, pk=job_id)
        results = ReconciliationResult.objects.filter(job=job).values(
            "store_code", "network_name", "business_name", "location_name",
            "identifier_value", "match_rule", "old_status", "new_status",
            "process_status", "status_changed", "change_note",
        )

        rows = list(results)
        if not rows:
            messages.error(request, "Tidak ada hasil rekonsiliasi untuk didownload.")
            return redirect("gbp:update_status")

        df = pd.DataFrame(rows)
        df.columns = [
            "Store Code", "Network Name", "Nama Bisnis", "Location Name",
            "Identifier", "Aturan Matching", "Status Lama", "Status Baru",
            "Status Proses", "Status Berubah?", "Catatan",
        ]

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"hasil_pencocokan_status_verifikasi_gbp_{ts}.csv"
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

        response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ══════════════════════════════════════════════════════════════════════
# PAGE 6 — DOWNLOAD REPORT
# ══════════════════════════════════════════════════════════════════════

class DownloadReportView(View):
    """Halaman daftar rekonsiliasi yang bisa didownload."""
    template_name = "gbp/download_report.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        ctx = _get_run_context(request)
        jobs = ReconciliationJob.objects.order_by("-created_at")[:20]
        ctx["recon_jobs"] = jobs

        return render(request, self.template_name, ctx)


class DownloadReconDetailView(View):
    """Download CSV rekonsiliasi tertentu dengan kolom: network_name, status lama, baru, latlong."""

    def get(self, request: HttpRequest, job_id: int) -> HttpResponse:
        job = get_object_or_404(ReconciliationJob, pk=job_id)
        results = ReconciliationResult.objects.filter(job=job).select_related()

        rows = []
        for r in results:
            # Ambil latlong dari snapshot terbaru
            lat, lng = None, None
            snap = None
            if r.store_code:
                snap = LocationSnapshot.objects.filter(store_code=r.store_code).order_by("-created_at").first()
            if not snap and r.business_name:
                snap = LocationSnapshot.objects.filter(business_name=r.business_name).order_by("-created_at").first()
            if not snap and r.identifier_value:
                # Fallback untuk job lama yang belum save store_code
                snap = LocationSnapshot.objects.filter(store_code=r.identifier_value).order_by("-created_at").first()
                if not snap:
                    snap = LocationSnapshot.objects.filter(business_name=r.identifier_value).order_by("-created_at").first()

            latlong_str = ""
            maps_uri_str = ""
            if snap and snap.latitude is not None and snap.longitude is not None:
                latlong_str = f"{snap.latitude},{snap.longitude}"
            if snap and snap.maps_uri:
                maps_uri_str = snap.maps_uri

            # Penyesuaian retroaktif untuk file CSV lama:
            # Jika old_status = "Need Reverification" dan new_status = "Need Verification"
            old_s = str(r.old_status or "").strip()
            new_s = str(r.new_status or "").strip()
            is_changed = r.status_changed
            catatan = r.change_note or ""

            if old_s.lower() == "need reverification" and new_s.lower() in ["need verification", "verification required"]:
                new_s = old_s
                is_changed = False
                catatan = "Tidak ada perubahan"

            rows.append({
                "Nama Network": r.network_name or r.business_name or r.identifier_value or "—",
                "Store Code": r.store_code or r.identifier_value or "—",
                "Status Lama": old_s or "—",
                "Status Baru": new_s or "—",
                "Status Berubah": "Ya" if is_changed else "Tidak",
                "Latlong": latlong_str,
                "URL Maps": maps_uri_str,
                "Catatan": catatan,
            })

        if not rows:
            messages.error(request, "Tidak ada data untuk didownload.")
            return redirect("gbp:download_report")

        df = pd.DataFrame(rows)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"rekonsiliasi_status_gbp_{ts}.csv"
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

        response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ══════════════════════════════════════════════════════════════════════
# API — FETCH TRIGGER & TREND DATA
# ══════════════════════════════════════════════════════════════════════

class TriggerFetchView(View):
    """Trigger fetch GBP API dan simpan ke database."""

    def post(self, request: HttpRequest):
        try:
            from gbp.services.gbp_api import fetch_records
            account_id = request.POST.get("account_id") or None
            records = fetch_records(account_id=account_id)

            if not records:
                messages.error(request, "Data API kosong.")
                return redirect("gbp:overview")

            run_id = history_service.save_run(records)
            messages.success(request, f"✅ Fetch selesai! {len(records)} lokasi disimpan.")
            return redirect("gbp:overview")
        except Exception as exc:
            log.exception("Error saat trigger fetch GBP")
            messages.error(request, f"Gagal menjalankan fetch: {exc}")
            return redirect("gbp:overview")


class TrendDataView(View):
    """Endpoint JSON untuk data tren 30 hari (dipakai oleh Chart.js)."""

    def get(self, request: HttpRequest) -> JsonResponse:
        days = int(request.GET.get("days", 30))
        trend = history_service.get_status_trend(days=days)
        return JsonResponse({"data": trend})
