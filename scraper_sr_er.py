"""
scraper_sr_er.py (v2)
-----------------------
Downloads 3 years of Grid-India (POSOCO) Daily PSP Reports by directly
constructing the archive URL for every single date, instead of scraping
links off a page (which only shows the ~25 most recent reports).

URL pattern (confirmed working):
    https://report.grid-india.in/index.php?p=Daily+Report/PSP+Report/
        {FY}/{Month Year}&dl={dd.mm.yy}_NLDC_PSP.{ext}

    FY        = Indian fiscal year folder, e.g. "2025-2026"
                (April YEAR -> March YEAR+1)
    Month Year= e.g. "May 2025"
    dd.mm.yy  = 2-digit year in the filename itself

Tries .xls first (smaller, structured), falls back to .pdf if .xls 404s.

NOTE: report.grid-india.in's robots.txt disallows automated crawling.
This script is rate-limited (SLEEP_BETWEEN_REQUESTS) and identifies
itself honestly in the User-Agent for that reason -- don't remove the
delay or run multiple copies in parallel.
"""

import os
import time
import requests
import urllib3
from datetime import datetime, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://report.grid-india.in/index.php"
OUT_XLS = "data/raw_excel"
OUT_PDF = "data/raw_pdfs"
LOG_FILE = "download_log.csv"

SLEEP_BETWEEN_REQUESTS = 1.5  # slower -- previous rate likely triggered a block
YEARS_BACK = 3

# Set this to skip straight past dates you've already confirmed are done,
# instead of looping through them (each skip is just a fast disk check, but
# this also lets you manually jump past a range you know is complete).
# Format: "YYYY-MM-DD", or None to start from YEARS_BACK ago as normal.
RESUME_FROM_DATE = "2025-05-22"

HEADERS = {
    "User-Agent": "EcoCompute-research-project/1.0 (IIT Kharagpur student project; "
                  "contact: your_email@iitkgp.ac.in)"
}

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def fiscal_year_folder(d: datetime) -> str:
    """April-March Indian fiscal year, e.g. 2025-04-15 -> '2025-2026',
    2026-02-10 -> '2025-2026'."""
    if d.month >= 4:
        return f"{d.year}-{d.year + 1}"
    else:
        return f"{d.year - 1}-{d.year}"


def build_url(d: datetime, ext: str):
    fy = fiscal_year_folder(d)
    month_folder = f"{MONTH_NAMES[d.month - 1]} {d.year}"
    filename = f"{d.strftime('%d.%m.%y')}_NLDC_PSP.{ext}"
    p_param = f"Daily Report/PSP Report/{fy}/{month_folder}"
    # requests handles the URL-encoding of spaces/slashes in params for us
    return BASE, {"p": p_param, "dl": filename}, filename


def try_download(d: datetime, ext: str, out_dir: str, session: requests.Session):
    """Returns (success: bool, was_blocked: bool). was_blocked=True means a
    connection-level failure (timeout/refused) -- a sign of rate-limiting,
    as opposed to a clean 404 which just means that day's file doesn't
    exist in that format."""
    base, params, filename = build_url(d, ext)
    out_path = os.path.join(out_dir, filename)
    if os.path.exists(out_path):
        return True, False  # already have it

    try:
        resp = session.get(base, params=params, headers=HEADERS,
                            verify=False, timeout=(10, 15))
    except requests.RequestException as e:
        print(f"    [network error on {filename}]: {type(e).__name__}", flush=True)
        return False, True

    if resp.status_code != 200 or resp.headers.get("Content-Type", "").startswith("text/html"):
        return False, False
    if len(resp.content) < 1000:  # real reports are always bigger than this
        return False, False

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return True, False


def main():
    end_date = datetime.now()
    start_date = end_date.replace(year=end_date.year - YEARS_BACK)
    if RESUME_FROM_DATE:
        resume_dt = datetime.strptime(RESUME_FROM_DATE, "%Y-%m-%d")
        if resume_dt > start_date:
            start_date = resume_dt

    total_days = (end_date - start_date).days + 1
    xls_count, pdf_count, missing = 0, 0, []
    consecutive_failures = 0

    print(f"Downloading reports from {start_date.date()} to {end_date.date()} "
          f"({total_days} days)...\n")

    session = requests.Session()

    d = start_date
    day_num = 0
    while d <= end_date:
        day_num += 1
        print(f"[{day_num}/{total_days}] {d.date()}...", end=" ", flush=True)

        got_xls, xls_blocked = try_download(d, "xls", OUT_XLS, session)
        if got_xls:
            xls_count += 1
            consecutive_failures = 0
            print("xls OK", flush=True)
        elif xls_blocked:
            # Connection-level failure -- pdf will almost certainly fail too
            # right now, so don't waste a second request confirming that.
            missing.append(d.strftime("%Y-%m-%d"))
            consecutive_failures += 1
            print("skipped pdf attempt (blocked)", flush=True)
        else:
            # Clean 404 on xls -- server is reachable, this format just
            # doesn't exist for this date, so pdf fallback is worth trying.
            got_pdf, pdf_blocked = try_download(d, "pdf", OUT_PDF, session)
            if got_pdf:
                pdf_count += 1
                consecutive_failures = 0
                print("pdf OK", flush=True)
            else:
                missing.append(d.strftime("%Y-%m-%d"))
                consecutive_failures += 1 if pdf_blocked else 0
                print("missing", flush=True)

        # Back off if the server seems to be rate-limiting/blocking us --
        # several failures in a row is a sign of that, not genuine gaps
        # in the data (which tend to be isolated single days).
        if consecutive_failures >= 5:
            wait = min(60 * (2 ** (consecutive_failures // 5 - 1)), 900)  # 60s, 120s, 240s... capped at 15 min
            print(f"  !! {consecutive_failures} failures in a row -- likely "
                  f"IP-blocked. Pausing {wait}s before continuing. If this keeps "
                  f"escalating, stop the script (Ctrl+C) and wait 30-60 min "
                  f"before resuming.", flush=True)
            time.sleep(wait)

        time.sleep(SLEEP_BETWEEN_REQUESTS)
        d += timedelta(days=1)

    print(f"\nDone.")
    print(f"  Excel downloaded: {xls_count}")
    print(f"  PDF fallback downloaded: {pdf_count}")
    print(f"  Missing (no report found either format): {len(missing)}")

    if missing:
        with open(LOG_FILE, "w") as f:
            f.write("missing_date\n")
            for m in missing:
                f.write(f"{m}\n")
        print(f"  Missing dates logged to {LOG_FILE} -- check these manually, "
              f"POSOCO occasionally skips holidays or has genuine gaps.")


if __name__ == "__main__":
    main()