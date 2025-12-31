# Use a Python version that has good wheel coverage
FROM python:3.13-slim

# (Optional but common) install build tools + basic libs
# Helps if any dep needs compilation or links to system libs.
RUN apt-get update && apt-get install -y \
    build-essential gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv + deps first (better caching)
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

# Copy your project after deps are installed
COPY . .

# Prefect will run your flow entrypoint; keep container alive-ready
# (command can be overridden by Prefect job variables)
CMD ["python", "-c", "print('Image built successfully')"]