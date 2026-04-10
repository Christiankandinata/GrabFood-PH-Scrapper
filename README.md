# GrabFood Philippines Merchant Scraper 🇵🇭

A web-based scraper tool that collects merchant data (including latitude/longitude) from GrabFood Philippines. Built with **FastAPI + Playwright**, designed for deployment on **Render** (free tier).

## What it scrapes

For each merchant, the scraper collects:
- **Merchant ID** & Chain ID
- **Name** & Chain Name
- **Latitude** & **Longitude**
- **Address**
- **Cuisines**
- **Rating**
- **Estimated delivery time**
- **Delivery fee**
- **Open/Closed status**
- **Photo URL**
- **Distance (km)**

## How it works

1. Launches a headless Chromium browser via Playwright
2. Sets location cookies for the target Philippine area
3. Navigates to `food.grab.com/ph/en/restaurants`
4. Extracts initial merchants from `__NEXT_DATA__` (Next.js SSR data)
5. Clicks "Load More" repeatedly, intercepting POST requests to `portal.grab.com/foodweb/v2/search`
6. Collects all merchant data from API responses
7. Presents results in a web UI with CSV/JSON export

## 20 Pre-configured Philippine locations

Manila, Makati, Quezon City, Cebu, Davao, Taguig (BGC), Pasig, Mandaluyong, Parañaque, Las Piñas, Caloocan, Pasay, San Juan, Marikina, Muntinlupa, Iloilo, Bacolod, Cagayan de Oro, Zamboanga, Baguio — plus custom lat/lng support.

## Local Development

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/grabfood-scraper.git
cd grabfood-scraper

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Run the app
python app.py
```

Open `http://localhost:10000` in your browser.

## Deploy to Render (Free)

### Option A: One-click with render.yaml

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → **Blueprint**
3. Connect your GitHub repo
4. Render detects `render.yaml` and deploys automatically

### Option B: Manual setup

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → **Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Runtime:** Docker
   - **Plan:** Free
   - **Health Check Path:** `/health`
5. Click **Create Web Service**

> ⚠️ **Render free tier notes:**
> - Service spins down after 15 min of inactivity (cold start ~30-60s)
> - 512 MB RAM — sufficient for scraping one location at a time
> - Playwright + Chromium adds ~400MB to the Docker image (first deploy takes ~5 min)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/locations` | List available Philippine locations |
| `POST` | `/api/scrape?location=manila` | Start a scrape job |
| `GET` | `/api/status/{job_id}` | Check job status |
| `GET` | `/api/results/{job_id}` | Get full results with merchant data |
| `GET` | `/api/export/{job_id}` | Download CSV |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/health` | Health check |

## Tech Stack

- **Backend:** Python, FastAPI, Playwright
- **Frontend:** Vanilla HTML/CSS/JS
- **Deployment:** Docker, Render
- **Browser:** Headless Chromium

## Disclaimer

This tool is intended for **market research and analysis** purposes only. Please be responsible:
- Respect Grab's Terms of Service
- Add reasonable delays between scrapes
- Don't overload their servers
- Use data for internal research only, not for republication

## License

MIT
