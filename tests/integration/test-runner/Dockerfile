FROM python:3.12-slim

WORKDIR /tests

# Only minimal deps — no pytest-homeassistant-custom-component
RUN pip install --no-cache-dir aiohttp pytest pytest-asyncio

# Tests are bind-mounted at runtime
CMD ["pytest", "integration/", "-v", "--tb=short", "--asyncio-mode=auto"]
