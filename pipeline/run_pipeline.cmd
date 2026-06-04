@echo off
:: ============================================================
::  Purplle Store Analytics Pipeline — Full Run Script
::  Runs L2 → L3 → L4 → L5 → L6 for BOTH stores
::  All visualizer videos saved to pipeline\output\viz\
::  Run from:  pipeline\  folder
::  Usage:     run_pipeline.cmd
:: ============================================================

setlocal enabledelayedexpansion

:: ── Working directory ─────────────────────────────────────────────────────────
set ROOT=%~dp0
cd /d "%ROOT%"

:: ── Output root for all visualizer videos ────────────────────────────────────
set VIZ_OUT=%ROOT%output\viz
mkdir "%VIZ_OUT%" 2>nul

echo.
echo ============================================================
echo  STEP 0 — Install all requirements
echo ============================================================
pip install -r L1_StoreLayout_selfannotate\requirements.txt
pip install -r L2_Detect\requirements.txt
pip install shapely
pip install -r L5_reid\requirements.txt
pip install gdown
pip install pydantic
pip install fastapi "uvicorn[standard]" httpx
pip install -r L7_api\requirements.txt
pip install requests
if errorlevel 1 (echo [ERROR] pip install failed & pause & exit /b 1)

:: ============================================================
echo.
echo ============================================================
echo  STEP 1 — L2 Detection  (Store 1)
echo ============================================================
cd "%ROOT%L2_Detect"

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 1 - zone.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 1 CAM 1 zone failed

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 2 - zone.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 1 CAM 2 zone failed

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 3 - entry.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 1 CAM 3 entry failed

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 5 - billing.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 1 CAM 5 billing failed

:: ── Store 2 ───────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  STEP 1 — L2 Detection  (Store 2)
echo ============================================================

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\entry 1.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 2 entry 1 failed

python run_l2.py ^
  --layout    "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --video     "..\L1_StoreLayout_selfannotate\input\Store 2\entry 2.mp4" ^
  --camera_id CAM_ENTRY_02 ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 2 entry 2 failed

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\billing_area.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 2 billing failed

python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\zone.mp4" ^
  --clip_start 2026-03-08T10:00:00Z
if errorlevel 1 echo [WARN] Store 2 zone failed

:: ============================================================
echo.
echo ============================================================
echo  STEP 2 — L3 Zone Assignment  (Store 1)
echo ============================================================
cd "%ROOT%L3_ZoneAssign"
mkdir output 2>nul

python run_l3.py ^
  --tracks_dir "..\L2_Detect\output\STORE_STORE_1" ^
  --layout     "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --output_dir "output\store1"
if errorlevel 1 echo [WARN] L3 Store 1 failed

echo.
echo ============================================================
echo  STEP 2 — L3 Zone Assignment  (Store 2)
echo ============================================================
python run_l3.py ^
  --tracks_dir "..\L2_Detect\output\STORE_STORE_2" ^
  --layout     "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --output_dir "output\store2"
if errorlevel 1 echo [WARN] L3 Store 2 failed

:: ============================================================
echo.
echo ============================================================
echo  STEP 3 — L4 Entry/Exit Detection  (Store 1)
echo ============================================================
cd "%ROOT%L4_entry"
mkdir output 2>nul

python run_l4.py ^
  --zone_events_dir "..\L3_ZoneAssign\output\store1" ^
  --layout          "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --output_dir      "output\store1"
if errorlevel 1 echo [WARN] L4 Store 1 failed

echo.
echo ============================================================
echo  STEP 3 — L4 Entry/Exit Detection  (Store 2)
echo ============================================================
python run_l4.py ^
  --zone_events_dir "..\L3_ZoneAssign\output\store2" ^
  --layout          "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --output_dir      "output\store2"
if errorlevel 1 echo [WARN] L4 Store 2 failed

:: ============================================================
echo.
echo ============================================================
echo  STEP 4 — L5 Staff Detection + Re-ID  (Store 1)
echo ============================================================
cd "%ROOT%L5_reid"
mkdir output 2>nul

python run_l5.py ^
  --tracks_dir   "..\L2_Detect\output\STORE_STORE_1" ^
  --entry_events "..\L4_entry\output\store1\CAM_ENTRY_01__CAM 3 - entry_zone_events_entry_events.jsonl" ^
  --videos_dir   "..\L1_StoreLayout_selfannotate\input\Store 1" ^
  --layout       "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --output_dir   "output\store1"
if errorlevel 1 echo [WARN] L5 Store 1 failed

echo.
echo ============================================================
echo  STEP 4 — L5 Staff Detection + Re-ID  (Store 2)
echo ============================================================
python run_l5.py ^
  --tracks_dir   "..\L2_Detect\output\STORE_STORE_2" ^
  --entry_events "..\L4_entry\output\store2\CAM_ENTRY_01__entry 1_zone_events_entry_events.jsonl" ^
  --videos_dir   "..\L1_StoreLayout_selfannotate\input\Store 2" ^
  --layout       "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --output_dir   "output\store2"
if errorlevel 1 echo [WARN] L5 Store 2 failed

:: ============================================================
echo.
echo ============================================================
echo  STEP 5 — L6 Event Emit  (Store 1)
echo ============================================================
cd "%ROOT%L6_emit"
mkdir output 2>nul

python run_l6.py ^
  --zone_events  "..\L3_ZoneAssign\output\store1\CAM_ZONE_01__CAM 1 - zone_zone_events.jsonl" ^
  --entry_events "..\L4_entry\output\store1\CAM_ENTRY_01__CAM 3 - entry_zone_events_entry_events.jsonl" ^
  --reid_events  "..\L5_reid\output\store1\reid_events.jsonl" ^
  --layout       "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --store_id     STORE_STORE_1 ^
  --output_dir   "output\store1"
if errorlevel 1 echo [WARN] L6 Store 1 failed

echo.
echo ============================================================
echo  STEP 5 — L6 Event Emit  (Store 2)
echo ============================================================
python run_l6.py ^
  --zone_events  "..\L3_ZoneAssign\output\store2\CAM_ZONE_01__zone_zone_events.jsonl" ^
  --entry_events "..\L4_entry\output\store2\CAM_ENTRY_01__entry 1_zone_events_entry_events.jsonl" ^
  --reid_events  "..\L5_reid\output\store2\reid_events.jsonl" ^
  --layout       "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --store_id     STORE_STORE_2 ^
  --output_dir   "output\store2"
if errorlevel 1 echo [WARN] L6 Store 2 failed

:: ============================================================
echo.
echo ============================================================
echo  STEP 6 — L7 API — Ingest both stores
echo ============================================================
cd "%ROOT%L7_api"
start "L7 API" uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000
echo Waiting 3 seconds for API to start...
timeout /t 3 /nobreak >nul

python -c "
import json, requests, sys, glob, os
files = glob.glob('../L6_emit/output/store*/events.jsonl')
for f in files:
    with open(f, encoding='utf-8') as fh:
        batch = [json.loads(l) for l in fh if l.strip()]
    if not batch:
        print(f'  [SKIP] empty: {f}')
        continue
    # Send in chunks of 500
    for i in range(0, len(batch), 500):
        chunk = batch[i:i+500]
        try:
            r = requests.post('http://localhost:8000/events/ingest', json=chunk, timeout=30)
            print(f'  {os.path.basename(f)} chunk {i//500+1}: {r.json()}')
        except Exception as e:
            print(f'  [ERROR] {e}')
"

:: ============================================================
echo.
echo ============================================================
echo  STEP 7 — Visualizer videos  (saved to output\viz\)
echo ============================================================
cd "%ROOT%L4_entry"

:: Store 1
python visualize.py ^
  --events "output\store1\CAM_ENTRY_01__CAM 3 - entry_zone_events_entry_events.jsonl" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 1\CAM 3 - entry.mp4" ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --tracks "..\L2_Detect\output\STORE_STORE_1\CAM_ENTRY_01__CAM 3 - entry.jsonl" ^
  --out    "%VIZ_OUT%\Store1_CAM_ENTRY_01.mp4"
if errorlevel 1 echo [WARN] Viz Store 1 entry failed

:: Store 2 cam 1
python visualize.py ^
  --events "output\store2\CAM_ENTRY_01__entry 1_zone_events_entry_events.jsonl" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\entry 1.mp4" ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --tracks "..\L2_Detect\output\STORE_STORE_2\CAM_ENTRY_01__entry 1.jsonl" ^
  --out    "%VIZ_OUT%\Store2_CAM_ENTRY_01.mp4"
if errorlevel 1 echo [WARN] Viz Store 2 entry 1 failed

:: Store 2 cam 2
python visualize.py ^
  --events "output\store2\CAM_ENTRY_02__entry 2_zone_events_entry_events.jsonl" ^
  --video  "..\L1_StoreLayout_selfannotate\input\Store 2\entry 2.mp4" ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 2\store_layout.json" ^
  --tracks "..\L2_Detect\output\STORE_STORE_2\CAM_ENTRY_02__entry 2.jsonl" ^
  --out    "%VIZ_OUT%\Store2_CAM_ENTRY_02.mp4"
if errorlevel 1 echo [WARN] Viz Store 2 entry 2 failed

:: ============================================================
echo.
echo ============================================================
echo  DONE
echo ============================================================
echo  Visualizer videos : %VIZ_OUT%\
echo  L6 final events   : %ROOT%L6_emit\output\
echo  API running at    : http://localhost:8000
echo  Health check      : http://localhost:8000/health
echo  Store 1 metrics   : http://localhost:8000/stores/STORE_STORE_1/metrics
echo  Store 2 metrics   : http://localhost:8000/stores/STORE_STORE_2/metrics
echo ============================================================
pause
