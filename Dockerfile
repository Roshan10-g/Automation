# Use official Playwright Python image (includes all Linux browser dependencies pre-installed)
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser
RUN playwright install chromium

# Copy all project code, configurations, and saved SSO profile
COPY . .

# Set timezone
ENV TZ=Asia/Kolkata

# Command to run bot continuously 24/7
CMD ["python", "bot.py"]
