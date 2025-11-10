# ctis-to-sigma


```bash
cd CTIS-TO-SIGMA
uv python install 3.12
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e apps/collector
uv run cts collect setup
```

```bash
uv run cts collect run -b base_url.txt -o data/urls.txt
uv run cts collect run -b base_url.txt -o data/urls.txt -m safe
uv run cts collect run -b base_url.txt -o data/urls.txt -m aggressive
CTS_MAX_CONCURRENT_SITES=4 CTS_MAX_CONCURRENT_PAGES=6 \
uv run cts collect run -b base_url.txt -o data/urls.txt
```
