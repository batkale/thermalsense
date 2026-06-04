from dotenv import load_dotenv
import os

load_dotenv()

OPEN_METEO_BASE      = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HIST_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SRTM_BASE         = "https://api.opentopodata.org/v1/srtm30m"
# OGN gliders come from the APRS TCP stream (aprs.glidernet.org:10152), not HTTP

# Bounding box — Greater Europe
LAT_MIN, LAT_MAX  = 35.0, 72.0
LON_MIN, LON_MAX  = -15.0, 45.0

OGN_FILTER_RADIUS = 2500          # km — radius for APRS stream filter

GRID_RES          = 0.0005        # degrees per cell (~50 m)
TERRAIN_RES       = 0.01          # coarse terrain fetch resolution (upsampled to GRID_RES)
UPDATE_INTERVAL   = 300           # seconds between OGN polls
MODEL_PATH        = "models/thermal_xgb.json"
BUFFER_PATH       = "models/training_buffer.npz"
