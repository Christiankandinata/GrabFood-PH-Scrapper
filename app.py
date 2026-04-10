"""
GrabFood Philippines Merchant Scraper
=====================================
FastAPI web app that scrapes merchant data (including lat/long) from GrabFood PH
by intercepting the internal portal.grab.com/foodweb/v2/search API calls.

Designed for deployment on Render (free tier).
"""

import asyncio
import json
import os
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from playwright.async_api import async_playwright

# Ensure static directory exists (no need for .gitkeep)
os.makedirs("static", exist_ok=True)

app = FastAPI(title="GrabFood PH Scraper", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# In-memory store for scrape results & status
# (For production, swap with Redis or a DB)
# ---------------------------------------------------------------------------
scrape_jobs: dict = {}

# ---------------------------------------------------------------------------
# Predefined locations across the Philippines
# ---------------------------------------------------------------------------
PH_LOCATIONS = {
    "manila": {"lat": 14.5995, "lng": 120.9842, "label": "Manila"},
    "makati": {"lat": 14.5547, "lng": 121.0244, "label": "Makati"},
    "quezon_city": {"lat": 14.6760, "lng": 121.0437, "label": "Quezon City"},
    "cebu": {"lat": 10.3157, "lng": 123.8854, "label": "Cebu City"},
    "davao": {"lat": 7.1907, "lng": 125.4553, "label": "Davao City"},
    "taguig": {"lat": 14.5176, "lng": 121.0509, "label": "Taguig (BGC)"},
    "pasig": {"lat": 14.5764, "lng": 121.0851, "label": "Pasig"},
    "mandaluyong": {"lat": 14.5794, "lng": 121.0359, "label": "Mandaluyong"},
    "paranaque": {"lat": 14.4793, "lng": 121.0198, "label": "Parañaque"},
    "las_pinas": {"lat": 14.4445, "lng": 120.9939, "label": "Las Piñas"},
    "caloocan": {"lat": 14.6500, "lng": 120.9667, "label": "Caloocan"},
    "pasay": {"lat": 14.5378, "lng": 121.0014, "label": "Pasay"},
    "san_juan": {"lat": 14.6019, "lng": 121.0355, "label": "San Juan"},
    "marikina": {"lat": 14.6507, "lng": 121.1029, "label": "Marikina"},
    "muntinlupa": {"lat": 14.4081, "lng": 121.0415, "label": "Muntinlupa"},
    "iloilo": {"lat": 10.7202, "lng": 122.5621, "label": "Iloilo City"},
    "bacolod": {"lat": 10.6840, "lng": 122.9563, "label": "Bacolod"},
    "cagayan_de_oro": {"lat": 8.4542, "lng": 124.6319, "label": "Cagayan de Oro"},
    "zamboanga": {"lat": 6.9214, "lng": 122.0790, "label": "Zamboanga City"},
    "baguio": {"lat": 16.4023, "lng": 120.5960, "label": "Baguio City"},
}


# ---------------------------------------------------------------------------
# Scraper Logic
# ---------------------------------------------------------------------------
async def scrape_grabfood(job_id: str, location_key: str, custom_lat: Optional[float] = None, custom_lng: Optional[float] = None):
    """
    Scrapes GrabFood PH merchants using a hybrid approach:
    1. Uses Playwright to visit the site, set localStorage (location), and capture valid headers
    2. Then directly calls portal.grab.com/foodweb/v2/search API with those headers
    3. Paginates through all results using the offset parameter
    """
    scrape_jobs[job_id]["status"] = "running"
    scrape_jobs[job_id]["message"] = "Launching browser..."

    merchants = {}

    if custom_lat and custom_lng:
        lat = custom_lat
        lng = custom_lng
        label = f"Custom ({lat}, {lng})"
    else:
        loc = PH_LOCATIONS.get(location_key, PH_LOCATIONS["manila"])
        lat = loc["lat"]
        lng = loc["lng"]
        label = loc["label"]

    scrape_jobs[job_id]["location"] = label

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ]
            )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-PH",
                geolocation={"latitude": lat, "longitude": lng},
                permissions=["geolocation"],
            )

            page = await context.new_page()

            # ---- Step 1: Visit the page to get a valid session ----
            scrape_jobs[job_id]["message"] = f"Opening GrabFood ({label})..."

            await page.goto("https://food.grab.com/ph/en/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            # ---- Step 2: Set localStorage with location data (this is the key!) ----
            scrape_jobs[job_id]["message"] = "Setting location via localStorage..."

            location_data = json.dumps({
                "latitude": lat,
                "longitude": lng,
                "address": label,
                "countryCode": "PH",
                "isAccurate": True,
                "addressDetail": label,
                "noteToDriver": "",
                "city": label,
            })

            await page.evaluate(f"""() => {{
                localStorage.setItem('location', JSON.stringify({location_data}));
                localStorage.setItem('gfc_country', 'PH');
            }}""")

            # ---- Step 3: Navigate to restaurants page with location set ----
            scrape_jobs[job_id]["message"] = "Loading restaurants page..."

            captured_headers = {}
            api_responses = []

            async def capture_request(request):
                nonlocal captured_headers
                if "portal.grab.com/foodweb/v2/search" in request.url:
                    captured_headers = dict(request.headers)

            async def capture_response(response):
                if "portal.grab.com/foodweb/v2/search" in response.url:
                    try:
                        body = await response.json()
                        api_responses.append(body)
                    except Exception:
                        pass

            page.on("request", capture_request)
            page.on("response", capture_response)

            await page.goto("https://food.grab.com/ph/en/restaurants", wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)

            # ---- Step 4: Extract from __NEXT_DATA__ ----
            scrape_jobs[job_id]["message"] = "Extracting initial page data..."

            try:
                next_data_raw = await page.evaluate("""() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }""")

                if next_data_raw:
                    next_data = json.loads(next_data_raw)
                    # Walk through all possible paths in the NEXT_DATA structure
                    found_merchants = find_merchants_in_data(next_data)
                    for m in found_merchants:
                        mid = m.get("id", m.get("chainID", "unknown"))
                        merchants[mid] = extract_merchant_data(m)

                    scrape_jobs[job_id]["message"] = f"Found {len(merchants)} merchants from page data"
                    scrape_jobs[job_id]["count"] = len(merchants)

            except Exception as e:
                scrape_jobs[job_id]["message"] = f"Initial extraction note: {str(e)[:80]}"

            # Also process any API responses captured during page load
            for resp in api_responses:
                found = find_merchants_in_data(resp)
                for m in found:
                    mid = m.get("id", m.get("chainID", "unknown"))
                    merchants[mid] = extract_merchant_data(m)

            scrape_jobs[job_id]["count"] = len(merchants)

            # ---- Step 5: Click Load More OR call API directly ----
            scrape_jobs[job_id]["message"] = f"Paginating... ({len(merchants)} merchants so far)"

            # Try clicking Load More button
            load_more_clicks = 0
            consecutive_fails = 0

            for i in range(150):  # safety limit
                try:
                    # Try multiple selectors for the Load More button
                    load_more_btn = None
                    for selector in [
                        'button.ant-btn.ant-btn-block',
                        'button:has-text("Load More")',
                        'button:has-text("Show More")',
                        '[class*="loadMore"]',
                        '[class*="load-more"]',
                        'button.RestaurantListCol___StyledButton',
                        'button[class*="RestaurantList"]',
                        'div[class*="RestaurantList"] button',
                    ]:
                        try:
                            btn = await page.query_selector(selector)
                            if btn:
                                is_visible = await btn.is_visible()
                                if is_visible:
                                    load_more_btn = btn
                                    break
                        except Exception:
                            continue

                    if not load_more_btn:
                        consecutive_fails += 1
                        if consecutive_fails >= 3:
                            break
                        # Try scrolling to bottom to reveal button
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(2000)
                        continue

                    consecutive_fails = 0

                    # Clear previous responses
                    prev_count = len(api_responses)

                    await load_more_btn.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    await load_more_btn.click()
                    load_more_clicks += 1

                    # Wait for new API response
                    await page.wait_for_timeout(4000)

                    # Process any new API responses
                    for resp in api_responses[prev_count:]:
                        found = find_merchants_in_data(resp)
                        for m in found:
                            mid = m.get("id", m.get("chainID", "unknown"))
                            merchants[mid] = extract_merchant_data(m)

                    scrape_jobs[job_id]["count"] = len(merchants)
                    scrape_jobs[job_id]["message"] = f"Loading... {len(merchants)} merchants ({load_more_clicks} pages)"

                    # Check if we got new results
                    if len(api_responses) == prev_count:
                        consecutive_fails += 1
                        if consecutive_fails >= 3:
                            break

                except Exception as e:
                    consecutive_fails += 1
                    if consecutive_fails >= 3:
                        break

            # ---- Step 6: Also try direct API calls if we captured headers ----
            if captured_headers and len(merchants) == 0:
                scrape_jobs[job_id]["message"] = "Trying direct API calls..."
                merchants = await try_direct_api(page, context, lat, lng, scrape_jobs, job_id)

            await browser.close()

        # Store results
        scrape_jobs[job_id]["status"] = "completed"
        scrape_jobs[job_id]["count"] = len(merchants)
        scrape_jobs[job_id]["merchants"] = list(merchants.values())
        scrape_jobs[job_id]["completed_at"] = datetime.now().isoformat()
        scrape_jobs[job_id]["message"] = f"Done! Scraped {len(merchants)} merchants from {label}"

    except Exception as e:
        scrape_jobs[job_id]["status"] = "error"
        scrape_jobs[job_id]["message"] = f"Error: {str(e)}"


def find_merchants_in_data(data, depth=0) -> list:
    """Recursively search for merchant arrays in nested data structures."""
    if depth > 10:
        return []

    merchants = []

    if isinstance(data, dict):
        # Direct match
        if "searchMerchants" in data:
            sm = data["searchMerchants"]
            if isinstance(sm, list):
                merchants.extend(sm)

        # Check if this dict itself looks like a merchant
        if "latlng" in data and ("id" in data or "chainID" in data):
            merchants.append(data)

        # Recurse into values
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                merchants.extend(find_merchants_in_data(value, depth + 1))

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                merchants.extend(find_merchants_in_data(item, depth + 1))

    return merchants


async def try_direct_api(page, context, lat, lng, scrape_jobs, job_id) -> dict:
    """
    Fallback: directly call the GrabFood search API from within the browser context
    to bypass any CORS or session issues.
    """
    merchants = {}
    offset = 0
    page_size = 32

    for attempt in range(50):  # max 50 pages
        try:
            result = await page.evaluate(f"""async () => {{
                try {{
                    const resp = await fetch('https://portal.grab.com/foodweb/v2/search', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'x-gfc-country': 'PH',
                        }},
                        body: JSON.stringify({{
                            "latlng": "{lat},{lng}",
                            "keyword": "",
                            "offset": {offset},
                            "pageSize": {page_size},
                            "countryCode": "PH"
                        }})
                    }});
                    return await resp.json();
                }} catch(e) {{
                    return {{"error": e.message}};
                }}
            }}""")

            if not result or "error" in result:
                break

            found = find_merchants_in_data(result)
            if not found:
                break

            for m in found:
                mid = m.get("id", m.get("chainID", "unknown"))
                merchants[mid] = extract_merchant_data(m)

            scrape_jobs[job_id]["count"] = len(merchants)
            scrape_jobs[job_id]["message"] = f"Direct API: {len(merchants)} merchants (page {attempt + 1})"

            offset += page_size

            # Small delay to be polite
            await page.wait_for_timeout(1500)

        except Exception:
            break

    return merchants


def extract_merchant_data(m: dict) -> dict:
    """Extract relevant fields from a merchant object."""
    latlng = m.get("latlng", {})
    address = m.get("address", {})
    eta = m.get("estimatedDeliveryTime", None)
    fee = m.get("estimatedDeliveryFee", {})

    cuisine_list = []
    cuisines = m.get("merchantBrief", {}).get("cuisine", []) or m.get("cuisines", [])
    if isinstance(cuisines, list):
        cuisine_list = cuisines
    elif isinstance(cuisines, str):
        cuisine_list = [cuisines]

    return {
        "id": m.get("id", ""),
        "name": m.get("address", {}).get("name", "") or m.get("chainName", "") or m.get("name", ""),
        "chain_id": m.get("chainID", ""),
        "chain_name": m.get("chainName", ""),
        "latitude": latlng.get("latitude"),
        "longitude": latlng.get("longitude"),
        "address": address.get("name", ""),
        "cuisines": cuisine_list,
        "rating": m.get("merchantBrief", {}).get("rating", None) or m.get("rating", None),
        "estimated_delivery_time": eta,
        "delivery_fee": fee.get("priceDisplay", "") if isinstance(fee, dict) else "",
        "promo": m.get("merchantBrief", {}).get("promo", {}) or {},
        "is_open": m.get("merchantBrief", {}).get("isOpen", None),
        "photo_url": m.get("merchantBrief", {}).get("photoHref", "") or m.get("photoHref", ""),
        "distance_km": m.get("merchantBrief", {}).get("distanceInKm", None) or m.get("distanceInKm", None),
    }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/locations")
async def get_locations():
    return JSONResponse(content=PH_LOCATIONS)


@app.post("/api/scrape")
async def start_scrape(
    background_tasks: BackgroundTasks,
    location: str = Query(default="manila"),
    custom_lat: Optional[float] = Query(default=None),
    custom_lng: Optional[float] = Query(default=None),
):
    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{location}"
    scrape_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "message": "Job queued...",
        "location": location,
        "count": 0,
        "merchants": [],
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }

    background_tasks.add_task(scrape_grabfood, job_id, location, custom_lat, custom_lng)
    return JSONResponse(content={"job_id": job_id, "status": "queued"})


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = scrape_jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    return JSONResponse(content={
        "job_id": job["job_id"],
        "status": job["status"],
        "message": job["message"],
        "location": job.get("location", ""),
        "count": job["count"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
    })


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    job = scrape_jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    return JSONResponse(content={
        "job_id": job["job_id"],
        "status": job["status"],
        "count": job["count"],
        "location": job.get("location", ""),
        "merchants": job.get("merchants", []),
    })


@app.get("/api/export/{job_id}")
async def export_csv(job_id: str):
    job = scrape_jobs.get(job_id)
    if not job or not job.get("merchants"):
        return JSONResponse(content={"error": "No data to export"}, status_code=404)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "id", "name", "chain_id", "chain_name", "latitude", "longitude",
        "address", "cuisines", "rating", "estimated_delivery_time",
        "delivery_fee", "is_open", "distance_km", "photo_url",
    ])
    writer.writeheader()
    for m in job["merchants"]:
        row = {**m}
        row["cuisines"] = ", ".join(row.get("cuisines", []))
        row.pop("promo", None)
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=grabfood_{job.get('location', 'export')}_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@app.get("/api/jobs")
async def list_jobs():
    jobs_summary = []
    for jid, job in scrape_jobs.items():
        jobs_summary.append({
            "job_id": jid,
            "status": job["status"],
            "location": job.get("location", ""),
            "count": job["count"],
            "created_at": job["created_at"],
            "completed_at": job["completed_at"],
        })
    return JSONResponse(content=jobs_summary)


# ---------------------------------------------------------------------------
# Health check for Render
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
