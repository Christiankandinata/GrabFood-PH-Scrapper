"""
GrabFood Philippines Merchant Scraper — v2
===========================================
Key fix: adds /api/debug endpoint to inspect exactly what the page returns.
Uses storageState approach + page reload for localStorage to take effect.
"""

import asyncio
import json
import os
import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grabscraper")

os.makedirs("static", exist_ok=True)

app = FastAPI(title="GrabFood PH Scraper", version="2.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

scrape_jobs: dict = {}

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
# Helper: launch browser with common config
# ---------------------------------------------------------------------------
async def launch_browser(pw):
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
        ]
    )
    return browser


async def create_context(browser, lat, lng):
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="en-PH",
        geolocation={"latitude": lat, "longitude": lng},
        permissions=["geolocation"],
    )
    return context


# ---------------------------------------------------------------------------
# DEBUG ENDPOINT — run this first to see what GrabFood returns!
# Visit: https://your-app.onrender.com/api/debug?location=quezon_city
# ---------------------------------------------------------------------------
@app.get("/api/debug")
async def debug_scrape(location: str = Query(default="quezon_city")):
    """
    Diagnostic endpoint: shows exactly what the page returns.
    Visit this URL in your browser to understand why 0 merchants.
    """
    loc = PH_LOCATIONS.get(location, PH_LOCATIONS["manila"])
    lat, lng, label = loc["lat"], loc["lng"], loc["label"]

    debug_info = {
        "location": label,
        "lat": lat,
        "lng": lng,
        "steps": [],
    }

    try:
        async with async_playwright() as p:
            browser = await launch_browser(p)
            context = await create_context(browser, lat, lng)
            page = await context.new_page()

            # Track API calls
            api_calls = []
            async def on_response(resp):
                if "grab.com" in resp.url and any(k in resp.url for k in ["search", "merchant", "foodweb"]):
                    try:
                        body = await resp.text()
                        api_calls.append({
                            "url": resp.url[:150],
                            "status": resp.status,
                            "body_preview": body[:500] if body else "EMPTY",
                        })
                    except:
                        api_calls.append({"url": resp.url[:150], "status": resp.status, "body_preview": "UNREADABLE"})
            page.on("response", on_response)

            # Step 1: Visit homepage
            debug_info["steps"].append("Step 1: Visiting homepage...")
            try:
                resp = await page.goto("https://food.grab.com/ph/en/", wait_until="domcontentloaded", timeout=30000)
                debug_info["steps"].append(f"  Homepage status: {resp.status if resp else 'NO RESPONSE'}")
                debug_info["steps"].append(f"  URL after load: {page.url}")
            except Exception as e:
                debug_info["steps"].append(f"  Homepage error: {str(e)[:200]}")

            await page.wait_for_timeout(3000)

            # Step 2: Check page content
            title = await page.title()
            debug_info["page_title"] = title

            body_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 1000) : 'NO BODY'")
            debug_info["page_body_preview"] = body_text

            # Step 3: Set localStorage
            debug_info["steps"].append("Step 2: Setting localStorage...")
            try:
                await page.evaluate("""(lat, lng, label) => {
                    const loc = {
                        latitude: lat,
                        longitude: lng,
                        address: label,
                        countryCode: "PH",
                        isAccurate: true,
                        addressDetail: label,
                        noteToDriver: "",
                        city: label
                    };
                    localStorage.setItem('location', JSON.stringify(loc));
                    localStorage.setItem('gfc_country', 'PH');
                }""", lat, lng, label)

                ls_data = await page.evaluate("""() => ({
                    location: localStorage.getItem('location'),
                    country: localStorage.getItem('gfc_country'),
                    allKeys: Object.keys(localStorage),
                    allData: JSON.stringify(localStorage)
                })""")
                debug_info["localStorage"] = {
                    "keys": ls_data["allKeys"],
                    "location_set": ls_data["location"] is not None,
                    "location_preview": (ls_data["location"] or "")[:200],
                }
                debug_info["steps"].append(f"  localStorage keys: {ls_data['allKeys']}")
            except Exception as e:
                debug_info["steps"].append(f"  localStorage error: {str(e)[:200]}")

            # Step 4: Reload page (so localStorage takes effect)
            debug_info["steps"].append("Step 3: Reloading page with localStorage set...")
            try:
                await page.reload(wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000)
                debug_info["steps"].append(f"  URL after reload: {page.url}")

                body_after = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 1000) : 'NO BODY'")
                debug_info["page_body_after_reload"] = body_after
            except Exception as e:
                debug_info["steps"].append(f"  Reload error: {str(e)[:200]}")

            # Step 5: Navigate to restaurants page
            debug_info["steps"].append("Step 4: Navigating to /restaurants...")
            try:
                await page.goto("https://food.grab.com/ph/en/restaurants", wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000)
                debug_info["steps"].append(f"  Restaurants URL: {page.url}")

                rest_body = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 1500) : 'NO BODY'")
                debug_info["restaurants_page_body"] = rest_body
            except Exception as e:
                debug_info["steps"].append(f"  Restaurants error: {str(e)[:200]}")

            # Step 6: Check __NEXT_DATA__
            debug_info["steps"].append("Step 5: Checking __NEXT_DATA__...")
            try:
                nd_raw = await page.evaluate("""() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent.substring(0, 5000) : 'NOT_FOUND';
                }""")
                if nd_raw != "NOT_FOUND":
                    # Try to parse it
                    full_nd = await page.evaluate("""() => {
                        const el = document.getElementById('__NEXT_DATA__');
                        return el ? el.textContent : null;
                    }""")
                    if full_nd:
                        nd = json.loads(full_nd)
                        debug_info["next_data_keys"] = list(nd.keys())
                        if "props" in nd:
                            props = nd["props"]
                            debug_info["next_data_props_keys"] = list(props.keys())
                            if "pageProps" in props:
                                pp = props["pageProps"]
                                debug_info["next_data_pageProps_keys"] = list(pp.keys())
                                # Show first 3000 chars
                                debug_info["next_data_pageProps_preview"] = json.dumps(pp)[:3000]

                                # Try to find merchants
                                found = find_merchants_in_data(nd)
                                debug_info["merchants_found_in_next_data"] = len(found)
                                if found:
                                    debug_info["sample_merchant"] = json.dumps(found[0])[:500]
                else:
                    debug_info["next_data"] = "NOT FOUND IN PAGE"
            except Exception as e:
                debug_info["steps"].append(f"  __NEXT_DATA__ error: {str(e)[:200]}")

            # Step 7: Check captured API calls
            debug_info["api_calls_captured"] = len(api_calls)
            debug_info["api_calls"] = api_calls[:10]

            # Step 8: Try to find restaurant cards in DOM
            debug_info["steps"].append("Step 6: Looking for restaurant elements in DOM...")
            try:
                restaurant_count = await page.evaluate("""() => {
                    const selectors = [
                        '[class*="RestaurantList"]',
                        '[class*="restaurant"]',
                        '[class*="merchant"]',
                        '[class*="vendor"]',
                        'a[href*="/restaurant/"]',
                        '[data-testid*="restaurant"]',
                        '[data-testid*="merchant"]',
                    ];
                    const results = {};
                    for (const sel of selectors) {
                        try {
                            results[sel] = document.querySelectorAll(sel).length;
                        } catch(e) {
                            results[sel] = 'error: ' + e.message;
                        }
                    }
                    return results;
                }""")
                debug_info["dom_restaurant_selectors"] = restaurant_count
            except Exception as e:
                debug_info["steps"].append(f"  DOM check error: {str(e)[:200]}")

            # Step 9: Take a screenshot
            debug_info["steps"].append("Step 7: Taking screenshot...")
            try:
                screenshot_path = "/app/static/debug_screenshot.png"
                if not os.path.exists("/app/static"):
                    screenshot_path = "static/debug_screenshot.png"
                await page.screenshot(path=screenshot_path, full_page=False)
                debug_info["screenshot"] = "/static/debug_screenshot.png"
            except Exception as e:
                debug_info["steps"].append(f"  Screenshot error: {str(e)[:200]}")

            # Step 10: Get all cookies
            cookies = await context.cookies()
            grab_cookies = [{"name": c["name"], "value": str(c["value"])[:80]} for c in cookies if "grab" in c.get("domain", "")]
            debug_info["cookies"] = grab_cookies

            await browser.close()

    except Exception as e:
        debug_info["fatal_error"] = str(e)

    return JSONResponse(content=debug_info)


# ---------------------------------------------------------------------------
# Scraper Logic (v2)
# ---------------------------------------------------------------------------
async def scrape_grabfood(job_id: str, location_key: str, custom_lat: Optional[float] = None, custom_lng: Optional[float] = None):
    scrape_jobs[job_id]["status"] = "running"
    scrape_jobs[job_id]["message"] = "Launching browser..."

    merchants = {}

    if custom_lat and custom_lng:
        lat, lng = custom_lat, custom_lng
        label = f"Custom ({lat}, {lng})"
    else:
        loc = PH_LOCATIONS.get(location_key, PH_LOCATIONS["manila"])
        lat, lng, label = loc["lat"], loc["lng"], loc["label"]

    scrape_jobs[job_id]["location"] = label
    logger.info(f"Starting scrape for {label} ({lat}, {lng})")

    try:
        async with async_playwright() as p:
            browser = await launch_browser(p)
            context = await create_context(browser, lat, lng)
            page = await context.new_page()

            api_responses = []

            async def capture_response(response):
                url = response.url
                if "grab.com" in url and ("search" in url or "foodweb" in url):
                    try:
                        body = await response.json()
                        api_responses.append(body)
                        logger.info(f"Captured API response from {url[:80]}")
                    except:
                        pass

            page.on("response", capture_response)

            # ---- Step 1: Visit homepage to establish session ----
            scrape_jobs[job_id]["message"] = "Opening GrabFood homepage..."
            await page.goto("https://food.grab.com/ph/en/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            logger.info(f"Homepage loaded: {page.url}")

            # ---- Step 2: Set localStorage with location ----
            scrape_jobs[job_id]["message"] = "Setting location..."
            await page.evaluate("""(lat, lng, label) => {
                const loc = {
                    latitude: lat, longitude: lng,
                    address: label, countryCode: "PH",
                    isAccurate: true, addressDetail: label,
                    noteToDriver: "", city: label
                };
                localStorage.setItem('location', JSON.stringify(loc));
                localStorage.setItem('gfc_country', 'PH');
            }""", lat, lng, label)

            # ---- Step 3: Reload so localStorage takes effect ----
            scrape_jobs[job_id]["message"] = "Reloading with location set..."
            await page.reload(wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)

            # ---- Step 4: Navigate to restaurants page ----
            scrape_jobs[job_id]["message"] = "Loading restaurants..."
            await page.goto("https://food.grab.com/ph/en/restaurants", wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            logger.info(f"Restaurants page loaded: {page.url}")

            # ---- Step 5: Extract __NEXT_DATA__ ----
            scrape_jobs[job_id]["message"] = "Extracting page data..."
            try:
                nd_raw = await page.evaluate("""() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }""")
                if nd_raw:
                    nd = json.loads(nd_raw)
                    found = find_merchants_in_data(nd)
                    for m in found:
                        mid = m.get("id", m.get("chainID", "unknown"))
                        merchants[mid] = extract_merchant_data(m)
                    logger.info(f"__NEXT_DATA__: found {len(found)} merchants")
            except Exception as e:
                logger.warning(f"__NEXT_DATA__ error: {e}")

            # Process any API responses from page load
            for resp in api_responses:
                for m in find_merchants_in_data(resp):
                    mid = m.get("id", m.get("chainID", "unknown"))
                    merchants[mid] = extract_merchant_data(m)

            scrape_jobs[job_id]["count"] = len(merchants)
            scrape_jobs[job_id]["message"] = f"Initial: {len(merchants)} merchants. Paginating..."

            # ---- Step 6: Click Load More repeatedly ----
            consecutive_fails = 0
            pages_loaded = 0
            prev_api_count = len(api_responses)

            for i in range(150):
                try:
                    # Try multiple selectors
                    btn = None
                    for sel in [
                        'button.ant-btn.ant-btn-block',
                        'button:has-text("Load More")',
                        'button:has-text("Show More")',
                        '[class*="loadMore"] button',
                        '[class*="load-more"] button',
                        'button[class*="RestaurantList"]',
                    ]:
                        try:
                            candidate = await page.query_selector(sel)
                            if candidate and await candidate.is_visible():
                                btn = candidate
                                break
                        except:
                            continue

                    if not btn:
                        # Try scrolling
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(2000)
                        consecutive_fails += 1
                        if consecutive_fails >= 3:
                            break
                        continue

                    consecutive_fails = 0
                    await btn.scroll_into_view_if_needed()
                    await page.wait_for_timeout(300)
                    await btn.click()
                    pages_loaded += 1

                    await page.wait_for_timeout(4000)

                    # Process new API responses
                    for resp in api_responses[prev_api_count:]:
                        for m in find_merchants_in_data(resp):
                            mid = m.get("id", m.get("chainID", "unknown"))
                            merchants[mid] = extract_merchant_data(m)
                    prev_api_count = len(api_responses)

                    scrape_jobs[job_id]["count"] = len(merchants)
                    scrape_jobs[job_id]["message"] = f"Loading... {len(merchants)} merchants ({pages_loaded} pages)"

                except Exception as e:
                    logger.warning(f"Load more error: {e}")
                    consecutive_fails += 1
                    if consecutive_fails >= 3:
                        break

            # ---- Step 7: Fallback — try direct API from browser ----
            if len(merchants) == 0:
                scrape_jobs[job_id]["message"] = "Trying direct API approach..."
                logger.info("No merchants from page. Trying direct API from browser context...")
                direct = await try_direct_api(page, lat, lng, scrape_jobs, job_id)
                merchants.update(direct)

            await browser.close()

        scrape_jobs[job_id]["status"] = "completed"
        scrape_jobs[job_id]["count"] = len(merchants)
        scrape_jobs[job_id]["merchants"] = list(merchants.values())
        scrape_jobs[job_id]["completed_at"] = datetime.now().isoformat()
        scrape_jobs[job_id]["message"] = f"Done! Scraped {len(merchants)} merchants from {label}"
        logger.info(f"Completed: {len(merchants)} merchants from {label}")

    except Exception as e:
        logger.error(f"Scrape error: {e}")
        scrape_jobs[job_id]["status"] = "error"
        scrape_jobs[job_id]["message"] = f"Error: {str(e)}"


def find_merchants_in_data(data, depth=0) -> list:
    if depth > 10:
        return []
    merchants = []
    if isinstance(data, dict):
        if "searchMerchants" in data and isinstance(data["searchMerchants"], list):
            merchants.extend(data["searchMerchants"])
        if "latlng" in data and ("id" in data or "chainID" in data):
            merchants.append(data)
        for v in data.values():
            if isinstance(v, (dict, list)):
                merchants.extend(find_merchants_in_data(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                merchants.extend(find_merchants_in_data(item, depth + 1))
    return merchants


async def try_direct_api(page, lat, lng, scrape_jobs, job_id) -> dict:
    merchants = {}
    offset = 0
    page_size = 32

    for attempt in range(50):
        try:
            result = await page.evaluate("""async (params) => {
                try {
                    const resp = await fetch('https://portal.grab.com/foodweb/v2/search', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'x-gfc-country': 'PH',
                        },
                        body: JSON.stringify({
                            latlng: params.lat + ',' + params.lng,
                            keyword: '',
                            offset: params.offset,
                            pageSize: params.pageSize,
                            countryCode: 'PH',
                        })
                    });
                    if (!resp.ok) return {error: 'HTTP ' + resp.status, status: resp.status};
                    return await resp.json();
                } catch(e) {
                    return {error: e.message};
                }
            }""", {"lat": str(lat), "lng": str(lng), "offset": offset, "pageSize": page_size})

            if not result or "error" in result:
                logger.info(f"Direct API stopped: {result}")
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
            await page.wait_for_timeout(1500)

        except Exception as e:
            logger.warning(f"Direct API error: {e}")
            break

    return merchants


def extract_merchant_data(m: dict) -> dict:
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
        "job_id": job_id, "status": "queued", "message": "Job queued...",
        "location": location, "count": 0, "merchants": [],
        "created_at": datetime.now().isoformat(), "completed_at": None,
    }
    background_tasks.add_task(scrape_grabfood, job_id, location, custom_lat, custom_lng)
    return JSONResponse(content={"job_id": job_id, "status": "queued"})


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = scrape_jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    return JSONResponse(content={
        "job_id": job["job_id"], "status": job["status"], "message": job["message"],
        "location": job.get("location", ""), "count": job["count"],
        "created_at": job["created_at"], "completed_at": job["completed_at"],
    })


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    job = scrape_jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    return JSONResponse(content={
        "job_id": job["job_id"], "status": job["status"],
        "count": job["count"], "location": job.get("location", ""),
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
        iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=grabfood_{job.get('location', 'export')}_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@app.get("/api/jobs")
async def list_jobs():
    return JSONResponse(content=[{
        "job_id": jid, "status": j["status"], "location": j.get("location", ""),
        "count": j["count"], "created_at": j["created_at"], "completed_at": j["completed_at"],
    } for jid, j in scrape_jobs.items()])


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
