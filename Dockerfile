# ---------- Stage: Runtime ----------
FROM python:3.11-slim

# Install system deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    libxkbcommon0 \
    libx11-xcb1 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Create static directory if it doesn't exist
RUN mkdir -p static

# Expose port (Render sets PORT env var, default 10000)
EXPOSE 10000

# Start the app
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
