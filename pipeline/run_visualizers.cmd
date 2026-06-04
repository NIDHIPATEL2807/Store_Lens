@echo off
:: ============================================================
::  Generate ALL visualizer videos — proves model is working
::  Run from:  pipeline\  folder
::  Output:    pipeline\output\viz\
:: ============================================================
setlocal enabledelayedexpansion
set ROOT=%~dp0
set VIZ=%ROOT%output\viz
mkdir "%VIZ%" 2>nul

:: ============================================================
echo.
echo ============================================================
echo  L2 — Detection + Tracking
echo ============================================================
cd "%ROOT%L2_Detect"

python visualize.py ^
  --jsonl "..\L2_Detect\output\STORE_STORE_1\CAM_ZONE_01__CAM 1 - zone.jsonl" ^
  --video "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 1 - zone.mp4" ^
  --out   "%VIZ%\L2_Store1_zone.mp4"
if errorlevel 1 echo [WARN] L2 Store1 zone viz failed

python visualize.py ^
  --jsonl "..\L2_Detect\output\STORE_STORE_1\CAM_ENTRY_01__CAM 3 - entry.jsonl" ^
  --video "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 3 - entry.mp4" ^
  --out   "%VIZ%\L2_Store1_entry.mp4"
if errorlevel 1 echo [WARN] L2 Store1 entry viz failed

python visualize.py ^
  --jsonl "..\L2_Detect\output\STORE_STORE_2\CAM_ZONE_01__zone.jsonl" ^
  --video "..\L1_StoreLayout_selfannotate\input\Store 2\zone.mp4" ^
  --out   "%VIZ%\L2_Store2_zone.mp4"
if errorlevel 1 echo [WARN] L2 Store2 zone viz failed

python visualize.py ^
  --jsonl "..\L2_Detect\output\STORE_STORE_2\CAM_ENTRY_01__entry 1.jsonl" ^
  --video "..\L1_StoreLayout_selfannotate\input\Store 2\entry 1.mp4" ^
  --out   "%VIZ%\L2_Store2_entry.mp4"
if errorlevel 1 echo [WARN] L2 Store2 entry viz failed

:: ============================================================
echo.
echo ============================================================
echo  L3 — Zone Assignment (heatmap + flash on zone enter/exit)
echo ============================================================
cd "%ROOT%L3_ZoneAssign"

python visualize.py ^
  --zone_events "output\store1\CAM_ZONE_01__CAM 1 - zone_zone_events.jsonl" ^
  --tracks      "..\L2_Detect\output\STORE_STORE_1\CAM_ZONE_01__CAM 1 - zone.jsonl" ^
  --layout      "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video       "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 1 - zone.mp4" ^
  --out         "%VIZ%\L3_Store1_zones.mp4"
if errorlevel 1 echo [WARN] L3 Store1 viz failed

python visualize.py ^
  --zone_events "output\store2\CAM_ZONE_01__zone_zone_events.jsonl" ^
  --tracks      "..\L2_Detect\output\STORE_STORE_2\CAM_ZONE_01__zone.jsonl" ^
  --layout      "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --video       "..\L1_StoreLayout_selfannotate\input\Store 2\zone.mp4" ^
  --out         "%VIZ%\L3_Store2_zones.mp4"
if errorlevel 1 echo [WARN] L3 Store2 viz failed

:: ============================================================
echo.
echo ============================================================
echo  L4 — Entry / Exit Events
echo ============================================================
cd "%ROOT%L4_entry"

python visualize.py ^
  --events "output\store1\CAM_ENTRY_01__CAM 3 - entry_zone_events_entry_events.jsonl" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 3 - entry.mp4" ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --tracks "..\L2_Detect\output\STORE_STORE_1\CAM_ENTRY_01__CAM 3 - entry.jsonl" ^
  --out    "%VIZ%\L4_Store1_entry.mp4"
if errorlevel 1 echo [WARN] L4 Store1 viz failed

python visualize.py ^
  --events "output\store2\CAM_ENTRY_01__entry 1_zone_events_entry_events.jsonl" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\entry 1.mp4" ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --tracks "..\L2_Detect\output\STORE_STORE_2\CAM_ENTRY_01__entry 1.jsonl" ^
  --out    "%VIZ%\L4_Store2_entry.mp4"
if errorlevel 1 echo [WARN] L4 Store2 viz failed

:: ============================================================
echo.
echo ============================================================
echo  L5 — Staff Detection + Re-ID
echo ============================================================
cd "%ROOT%L5_reid"

python visualize.py ^
  --reid_events "output\store1\reid_events.jsonl" ^
  --tracks_dir  "..\L2_Detect\output\STORE_STORE_1" ^
  --videos_dir  "..\L1_StoreLayout_selfannotate\input\Store 1" ^
  --layout      "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --out_dir     "%VIZ%\L5_Store1"
if errorlevel 1 echo [WARN] L5 Store1 viz failed

python visualize.py ^
  --reid_events "output\store2\reid_events.jsonl" ^
  --tracks_dir  "..\L2_Detect\output\STORE_STORE_2" ^
  --videos_dir  "..\L1_StoreLayout_selfannotate\input\Store 2" ^
  --layout      "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --out_dir     "%VIZ%\L5_Store2"
if errorlevel 1 echo [WARN] L5 Store2 viz failed

:: ============================================================
echo.
echo ============================================================
echo  DONE — all visualizer videos saved
echo ============================================================
echo  Output folder: %VIZ%
echo.
echo  Videos generated:
echo    L2_Store1_zone.mp4       — tracking bboxes (Store 1 zone cam)
echo    L2_Store1_entry.mp4      — tracking bboxes (Store 1 entry cam)
echo    L2_Store2_zone.mp4       — tracking bboxes (Store 2 zone cam)
echo    L2_Store2_entry.mp4      — tracking bboxes (Store 2 entry cam)
echo    L3_Store1_zones.mp4      — zone polygons flashing on person entry
echo    L3_Store2_zones.mp4      — zone polygons flashing on person entry
echo    L4_Store1_entry.mp4      — ENTRY/EXIT banners at gate
echo    L4_Store2_entry.mp4      — ENTRY/EXIT banners at gate
echo    L5_Store1\               — visitor IDs + STAFF badges
echo    L5_Store2\               — visitor IDs + STAFF badges
echo ============================================================
pause
