FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    mcp \
    fastapi \
    uvicorn \
    google-api-python-client \
    google-auth-httplib2 \
    google-auth-oauthlib \
    starlette \
    python-dotenv \
    httpx \
    beautifulsoup4

# Copy source code and version file
COPY mcp_server.py /app/mcp_server.py
COPY cli.py /app/cli.py
COPY version.txt* /app/

# Expose port
EXPOSE 8000

# Set environment variables
ENV HOST=0.0.0.0
ENV PORT=8000

# Run server with unbuffered logging
CMD ["python", "-u", "mcp_server.py"]
