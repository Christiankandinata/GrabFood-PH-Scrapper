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
    Scrapes GrabFood PH merchants by:
    1. Navigating to food.grab.com/ph/en/restaurants
    2. Intercepting POST requests to portal.grab.com/foodweb/v2/search
    3. Extracting merchant data including lat/long from __NEXT_DATA__ and API responses
    """
    scrape_jobs[job_id]["status"] = "running"
    scrape_jobs[job_id]["message"] = "Launching browser..."

    merchants = {}
    api_responses = []

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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-PH",
                geolocation={"latitude": lat, "longitude": lng},
                permissions=["geolocation"],
            )

            page = await context.new_page()

            # Intercept API responses
            async def handle_response(response):
                if "portal.grab.com/foodweb/v2/search" in response.url:
                    try:
                        body = await response.json()
                        api_responses.append(body)
                    except Exception:
                        pass

            page.on("response", handle_response)

            scrape_jobs[job_id]["message"] = f"Navigating to GrabFood ({label})..."

            # Set location cookie/localStorage before navigating
            await context.add_cookies([
                {
                    "name": "location",
                    "value": json.dumps({
                        "latitude": lat,
                        "longitude": lng,
                        "address": label,
                        "countryCode": "PH",
                        "isAccurate": True,
                    }),
                    "domain": "food.grab.com",
                    "path": "/",
                },
                {
                    "name": "gfc_country",
                    "value": "PH",
                    "domain": "food.grab.com",
                    "path": "/",
                },
                {
                    "name": "gfc_session_guid",
                    "value": "not-a-real-session",
                    "domain": "food.grab.com",
                    "path": "/",
                },
            ])

            url = f"https://food.grab.com/ph/en/restaurants"
            await page.goto(url, wait_until="networkidle", timeout=60000)

            scrape_jobs[job_id]["message"] = "Page loaded. Extracting __NEXT_DATA__..."

            # ---- Step 1: Extract from __NEXT_DATA__ (first batch) ----
            try:
                next_data_raw = await page.evaluate("""
                    () => {
                        const el = document.getElementById('__NEXT_DATA__');
                        return el ? el.textContent : null;
                    }
                """)
                if next_data_raw:
                    next_data = json.loads(next_data_raw)
                    # Navigate the nested structure to find merchants
                    props = next_data.get("props", {})
                    page_props = props.get("pageProps", {}) if props else {}

                    # Try multiple possible paths
                    search_result = (
                        page_props.get("searchResult", {}) or
                        page_props.get("initialState", {}).get("searchResult", {}) or
                        {}
                    )
                    search_merchants = search_result.get("searchMerchants", [])

                    if not search_merchants:
                        # Try deeper nesting
                        redux = page_props.get("initialReduxState", {})
                        if redux:
                            search_merchants = (
                                redux.get("pageRestaurantsV2", {}).get("searchMerchants", []) or
                                redux.get("searchResult", {}).get("searchMerchants", []) or
                                []
                            )

                    for m in search_merchants:
                        mid = m.get("id", m.get("chainID", "unknown"))
                        merchants[mid] = extract_merchant_data(m)

                    scrape_jobs[job_id]["message"] = f"Found {len(merchants)} merchants from initial page data"

            except Exception as e:
                scrape_jobs[job_id]["message"] = f"__NEXT_DATA__ extraction: {str(e)[:100]}"

            # ---- Step 2: Click "Load More" to trigger API calls ----
            scrape_jobs[job_id]["message"] = "Clicking 'Load More' to fetch all merchants..."

            load_more_clicks = 0
            max_clicks = 100  # Safety limit

            for i in range(max_clicks):
                try:
                    # Look for the load more button
                    load_more_btn = await page.query_selector(
                        'button.ant-btn.ant-btn-block, '
                        'button:has-text("Load More"), '
                        'button:has-text("Show More"), '
                        '[class*="loadMore"], '
                        '[class*="load-more"]'
                    )

                    if not load_more_btn:
                        break

                    is_visible = await load_more_btn.is_visible()
                    if not is_visible:
                        break

                    await load_more_btn.scroll_into_view_if_needed()
                    await load_more_btn.click()
                    load_more_clicks += 1

                    # Wait for API response
                    await page.wait_for_timeout(3000)

                    # Process captured API responses
                    for resp in api_responses:
                        search_result = resp.get("searchResult", {})
                        search_merchants = search_result.get("searchMerchants", [])
                        for m in search_merchants:
                            mid = m.get("id", m.get("chainID", "unknown"))
                            merchants[mid] = extract_merchant_data(m)

                    scrape_jobs[job_id]["message"] = f"Loaded {len(merchants)} merchants ({load_more_clicks} pages)..."
                    scrape_jobs[job_id]["count"] = len(merchants)

                except Exception:
                    break

            # Final process of any remaining API responses
            for resp in api_responses:
                search_result = resp.get("searchResult", {})
                search_merchants = search_result.get("searchMerchants", [])
                for m in search_merchants:
                    mid = m.get("id", m.get("chainID", "unknown"))
                    merchants[mid] = extract_merchant_data(m)

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
