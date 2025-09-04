"""
ParentZone Gallery Downloader – Parallel + Progress Bar + EXIF + CSV logging

Features
- You log in via Chrome, open the gallery, press Enter.
- Scrapes largest image URLs and downloads them in parallel with a progress bar.
- Retries transient failures (HTTP + per-file).
- Writes EXIF DateTimeOriginal from the `u=` timestamp and GPS (nursery coords).
- CSV log with successes/failures; supports --retry-failed mode (no browser needed).

Usage
  Normal run:
      python3 downloader.py
  Retry only failures from previous log:
      python3 downloader.py --retry-failed
  Useful flags:
      --workers 8            # parallelism (default 8)
      --out-dir Photos       # output folder
      --log-file pz_log.csv  # CSV path
      --no-prompt            # skip interactive retry at end
      --skip-exif            # disable EXIF writing
"""

import argparse
import csv
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry  # requests >=2.32
except Exception:
    from requests.packages.urllib3.util.retry import Retry  # fallback

import piexif
from PIL import Image
from tqdm import tqdm

# Selenium only needed for fresh scrape
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# ---------------- Configuration defaults ----------------
HOME_URL = "https://www.parentzone.me/"
DEFAULT_OUT_DIR = Path("parentzone_gallery")
DEFAULT_LOG = Path("download_log.csv")
IMG_SELECTOR = "img.css-10xjygp-image, .css-16epjnj-galleryContainer img, picture img"

# Nursery GPS coordinates (edit if needed, or override via CLI)
DEFAULT_LAT = 51.49009034271866
DEFAULT_LON = -3.163831280770506

# HTTP + per-file retry defaults
HTTP_RETRY_TOTAL = 5
HTTP_BACKOFF = 1.0  # exponential: 1,2,4,8…
PER_FILE_TRIES = 5  # wrapper retries around a single GET/EXIF/save

# ---------------- Retry-enabled session factory ----------------
def make_retry_session(base_headers: Optional[Dict[str, str]] = None) -> requests.Session:
    retry = Retry(
        total=HTTP_RETRY_TOTAL,
        connect=HTTP_RETRY_TOTAL,
        read=HTTP_RETRY_TOTAL,
        status=HTTP_RETRY_TOTAL,
        allowed_methods=frozenset(["GET", "HEAD"]),
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=HTTP_BACKOFF,
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    s = requests.Session()
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    if base_headers:
        s.headers.update(base_headers)
    return s

# ---------------- EXIF helpers ----------------
def parse_timestamp_from_u(url: str) -> Optional[datetime]:
    """Parse u=YYYY-MM-DDTHH:MM:SS (naive) from query string."""
    try:
        qs = parse_qs(urlparse(url).query)
        u = qs.get("u", [None])[0]
        if not u:
            return None
        return datetime.fromisoformat(u.rstrip("Z"))
    except Exception:
        return None

def deg_to_dms_rationals(deg_float: float):
    sign = 1 if deg_float >= 0 else -1
    deg_float = abs(deg_float)
    d = int(deg_float)
    m_float = (deg_float - d) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    d_rat = (d, 1)
    m_rat = (m, 1)
    s_frac = Fraction(s).limit_denominator(10000)
    s_rat = (s_frac.numerator, s_frac.denominator)
    return d_rat, m_rat, s_rat, sign

def write_exif_datetime_gps(jpeg_bytes: bytes, dt: Optional[datetime],
                            lat: Optional[float], lon: Optional[float]) -> bytes:
    """Return JPEG bytes with EXIF date+GPS written, using Pillow for a clean container."""
    try:
        exif_dict = piexif.load(jpeg_bytes)
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    if dt is not None:
        exif_str = dt.strftime("%Y:%m:%d %H:%M:%S")
        exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_str

    if lat is not None and lon is not None:
        d_lat, m_lat, s_lat, lat_sign = deg_to_dms_rationals(lat)
        d_lon, m_lon, s_lon, lon_sign = deg_to_dms_rationals(lon)
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat_sign >= 0 else b"S"
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = [d_lat, m_lat, s_lat]
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon_sign >= 0 else b"W"
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = [d_lon, m_lon, s_lon]
        exif_dict["GPS"][piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)

    exif_bytes = piexif.dump(exif_dict)

    im = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
    out_io = BytesIO()
    im.save(out_io, format="JPEG", exif=exif_bytes, quality=95)
    return out_io.getvalue()

# ---------------- Scrape helpers ----------------
def pick_largest_from_srcset(srcset: str) -> Optional[str]:
    best_url, best_w = None, -1
    for cand in srcset.split(","):
        cand = cand.strip()
        m = re.match(r"(\S+)\s+(\d+)w$", cand)
        if m:
            w = int(m.group(2))
            if w > best_w:
                best_url, best_w = m.group(1), w
        elif best_url is None and re.match(r"^https?://", cand):
            best_url = cand
    return best_url

def filename_from_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    parts = p.path.strip("/").split("/")
    name = "image"
    try:
        i = parts.index("media")
        media_id = parts[i+1]
        variant  = parts[i+2] if len(parts) > i+2 else "file"
        name = f"{media_id}_{variant}"
    except Exception:
        name = parts[-1] or "image"
    return f"{name}.jpg"

def extract_media_info(url: str) -> Tuple[str, str]:
    """Return (media_id, variant) if present; else ('', '')."""
    try:
        p = urllib.parse.urlparse(url)
        parts = p.path.strip("/").split("/")
        i = parts.index("media")
        media_id = parts[i+1]
        variant  = parts[i+2] if len(parts) > i+2 else "file"
        return media_id, variant
    except Exception:
        return "", ""

# ---------------- CSV logging ----------------
def ensure_log_header(path: Path):
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "timestamp", "status", "attempts", "http_status",
                "media_id", "variant", "filename", "url", "error"
            ])

_log_lock = threading.Lock()
def append_log(path: Path, row: Dict):
    with _log_lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                row.get("timestamp",""),
                row.get("status",""),
                row.get("attempts",0),
                row.get("http_status",""),
                row.get("media_id",""),
                row.get("variant",""),
                row.get("filename",""),
                row.get("url",""),
                row.get("error",""),
            ])

def read_failures_from_log(path: Path) -> List[str]:
    urls = []
    if not path.exists():
        return urls
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("status") != "success":
                u = row.get("url")
                if u:
                    urls.append(u)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ---------------- Core download (thread-friendly) ----------------
def download_one(url: str,
                 base_headers: Dict[str, str],
                 out_dir: Path,
                 write_exif_flag: bool,
                 lat: Optional[float],
                 lon: Optional[float],
                 referer: Optional[str],
                 max_tries: int = PER_FILE_TRIES) -> Tuple[bool, int, Optional[int], str, Optional[str]]:
    """
    Download a single URL with retries, EXIF write, and save.
    Returns: (success, attempts, http_status, filename, error_message)
    """
    session = make_retry_session(base_headers)  # per-thread/session safe
    fname = filename_from_url(url)
    out_path = out_dir / fname
    http_status = None
    error_message = None

    headers = {}
    if referer:
        headers["Referer"] = referer

    for attempt in range(1, max_tries + 1):
        try:
            r = session.get(url, timeout=30, headers=headers)
            http_status = r.status_code
            if r.ok and r.content:
                data = r.content
                if write_exif_flag:
                    try:
                        dt = parse_timestamp_from_u(url)
                        data = write_exif_datetime_gps(data, dt, lat, lon)
                    except Exception as ex:
                        # Not fatal
                        error_message = f"EXIF write failed: {ex}"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(data)

                # Try to set OS timestamp to EXIF time if present
                try:
                    dt = parse_timestamp_from_u(url)
                    if dt is not None:
                        ts = dt.timestamp()
                        os.utime(out_path, (ts, ts))
                except Exception:
                    pass

                return True, attempt, http_status, fname, None
            else:
                error_message = f"HTTP {r.status_code}"
        except Exception as ex:
            error_message = str(ex)

        # Backoff with tiny jitter
        sleep_s = (2 * attempt) + random.uniform(0, 0.5)
        time.sleep(sleep_s)

    return False, max_tries, http_status, fname, error_message

# ---------------- Selenium-driven scrape ----------------
def collect_urls_via_browser() -> Tuple[List[str], Dict[str, str], str]:
    """Open Chrome, let user log in & navigate, then scrape image URLs. Returns (urls, base_headers, referer)."""
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        driver.get(HOME_URL)
        print("\n=== Please log in to ParentZone in Chrome, open the Gallery, then press Enter here ===")
        input()

        # Nudge lazy-loaders
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, IMG_SELECTOR)))
        except Exception:
            pass

        last_h = 0
        for _ in range(15):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.6)
            h = driver.execute_script("return document.body.scrollHeight;")
            if h == last_h:
                break
            last_h = h
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            for _ in range(250):
                body.send_keys(Keys.ARROW_RIGHT)
                time.sleep(0.04)
        except Exception:
            pass

        urls = set()
        imgs = driver.find_elements(By.CSS_SELECTOR, IMG_SELECTOR)
        for el in imgs:
            srcset = el.get_attribute("srcset")
            src = el.get_attribute("src")
            u = pick_largest_from_srcset(srcset) if srcset else src
            if u and "api.parentzone.me" in u:
                urls.add(u)
        urls = list(urls)
        print(f"Found {len(urls)} images")

        # Build base headers from the live browser
        ua = driver.execute_script("return navigator.userAgent;")
        base_headers = {"User-Agent": ua}
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in driver.get_cookies()])
        if cookie_header:
            base_headers["Cookie"] = cookie_header

        referer = driver.current_url
        return urls, base_headers, referer

    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ---------------- CLI + main ----------------
def main():
    parser = argparse.ArgumentParser(description="ParentZone Gallery Downloader (parallel, progress bar, EXIF, CSV)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for images")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG), help="CSV log file")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT, help="GPS latitude to embed")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON, help="GPS longitude to embed")
    parser.add_argument("--skip-exif", action="store_true", help="Do not write EXIF date/GPS")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed URLs from a previous CSV log")
    parser.add_argument("--no-prompt", action="store_true", help="Skip interactive retry prompt at the end")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel download workers (default 8)")
    parser.add_argument("--max-tries", type=int, default=PER_FILE_TRIES, help="Per-file attempts (wrapper retries)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    log_file = Path(args.log_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_log_header(log_file)

    # URLs + headers
    if args.retry_failed:
        urls = read_failures_from_log(log_file)
        if not urls:
            print("No failed URLs found in log; nothing to retry.")
            return
        print(f"Retrying {len(urls)} previously failed URLs...")
        base_headers = {}  # we may not have cookies/UA; most URLs include signed keys
        referer = HOME_URL
    else:
        urls, base_headers, referer = collect_urls_via_browser()
        if not urls:
            print("No images detected; ensure the gallery is fully loaded before pressing Enter.")
            return

    # Parallel download with progress bar
    successes = 0
    failures = 0
    failed_urls: List[str] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                download_one,
                url,
                base_headers,
                out_dir,
                not args.skip_exif,
                args.lat,
                args.lon,
                referer,
                args.max_tries,
            ): url for url in urls
        }

        for fut in tqdm(as_completed(futures), total=len(futures), unit="img", desc="Downloading"):
            url = futures[fut]
            ok, attempts, http_status, fname, err = fut.result()
            media_id, variant = extract_media_info(url)

            row = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "status": "success" if ok else "failed",
                "attempts": attempts,
                "http_status": http_status if http_status is not None else "",
                "media_id": media_id,
                "variant": variant,
                "filename": fname or filename_from_url(url),
                "url": url,
                "error": "" if ok else (err or ""),
            }
            append_log(log_file, row)

            if ok:
                successes += 1
            else:
                failures += 1
                failed_urls.append(url)

    # Summary
    print("\n--- Summary ---")
    print(f"Total: {len(urls)}  |  Saved: {successes}  |  Failed: {failures}")
    print(f"Log: {log_file.resolve()}")

    # Optional interactive retry (only for fresh scrape runs)
    if (not args.retry_failed) and (failures > 0) and (not args.no_prompt):
        ans = input("Retry the failed downloads now? [y/N]: ").strip().lower()
        if ans == "y":
            print(f"Retrying {len(failed_urls)} failed items...")
            random.shuffle(failed_urls)
            retry_success = 0
            retry_fail = 0

            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                futures = {
                    executor.submit(
                        download_one,
                        url,
                        base_headers,
                        out_dir,
                        not args.skip_exif,
                        args.lat,
                        args.lon,
                        referer,
                        args.max_tries,
                    ): url for url in failed_urls
                }
                for fut in tqdm(as_completed(futures), total=len(futures), unit="img", desc="Retrying"):
                    url = futures[fut]
                    ok, attempts, http_status, fname, err = fut.result()
                    media_id, variant = extract_media_info(url)

                    row = {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "status": "success" if ok else "failed",
                        "attempts": attempts,
                        "http_status": http_status if http_status is not None else "",
                        "media_id": media_id,
                        "variant": variant,
                        "filename": fname or filename_from_url(url),
                        "url": url,
                        "error": "" if ok else (err or ""),
                    }
                    append_log(log_file, row)

                    if ok:
                        retry_success += 1
                    else:
                        retry_fail += 1

            print(f"Retry complete. Recovered: {retry_success}  |  Still failing: {retry_fail}")
            print(f"Updated log: {log_file.resolve()}")
        else:
            print("Okay, not retrying now. You can run again later with --retry-failed.")

if __name__ == "__main__":
    main()