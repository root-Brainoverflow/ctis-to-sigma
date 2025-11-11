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
cts collect run -b base_url.txt -o data/urls.txt
cts collect run -b base_url.txt -o data/urls.txt -m safe
cts collect run -b base_url.txt -o data/urls.txt -m aggressive
CTS_MAX_CONCURRENT_SITES=4 CTS_MAX_CONCURRENT_PAGES=6 \
cts collect run -b base_url.txt -o data/urls.txt
```
