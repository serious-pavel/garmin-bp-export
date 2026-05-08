# Garmin Blood Pressure Export

Small CLI tool for exporting Garmin Connect blood-pressure readings to CSV.

It uses the `garminconnect` Python package, logs into Garmin Connect, requests
the blood-pressure history for a date range, and writes a CSV with:

`datetime, systolic, diastolic, comment`

## Install

```bash
pip install -r requirements.txt
```

## Credentials

Create a `.env` file in the project root:

```dotenv
GARMIN_EMAIL=your.email@example.com
GARMIN_PASSWORD=your-password
```

## Usage

```bash
python garmin_bp_export.py \
  --start 2026-01-01 \
  --end 2026-01-31 \
  --output bp.csv
```

The script loads `.env` automatically. You can still override values with
`--email` and `--password` if needed.

By default, CSV files are written to `files/`. Use `--output-dir` to change the
folder, or pass a full path to `--output`.

Add `--debug-json` to save the raw Garmin response as a `.json` file next to
the CSV.

The script now reads the actual measurement entries from Garmin's response, so
the CSV includes the real measurement time when Garmin provides it.

## Notes

Garmin Connect itself supports exporting blood-pressure reports as CSV from the
web UI, but this script is useful when you want a repeatable export flow for a
specific period and further analysis. 
