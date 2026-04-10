# Use Microsoft's official Playwright image — all browser deps pre-installed
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers already included in base image, just ensure chromium is there
RUN playwright install chromium

# Copy application code
COPY . .

# Create static directory if it doesn't exist
RUN mkdir -p static

# Expose port (Render sets PORT env var, default 10000)
EXPOSE 10000

# Start the app
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
