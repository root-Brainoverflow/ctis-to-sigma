# ctis-to-sigma

CTI (Cyber Threat Intelligence) data collection, extraction, and parsing toolkit.

## Installation

### 1. Clone and setup environment
```bash
cd ctis-to-sigma
uv venv
source .venv/bin/activate
uv sync
```

### 2. Install browser dependencies
```bash
cts setup --install
```

This installs:
- **Playwright**: Browser automation library
- **Chromium**: Headless browser for rendering and PDF export
- System dependencies required by Chromium (fonts, libraries, etc.)

## Usage

### Collect URLs
Collect CTI article/report links from seed URLs:
```bash
# Basic usage
cts collect -b base_url.txt -o data/urls.txt

# Safe mode (lower concurrency)
cts collect -b base_url.txt -o data/urls.txt -m safe

# Aggressive mode (higher concurrency)
cts collect -b base_url.txt -o data/urls.txt -m aggressive

# Custom concurrency via environment variables
CTS_MAX_CONCURRENT_SITES=4 CTS_MAX_CONCURRENT_PAGES=6 \
cts collect -b base_url.txt -o data/urls.txt
```

**Options:**
- `-b, --base-url-file`: Seed URL list file (default: `base_url.txt`)
- `-o, --out-file`: Output file for collected URLs (default: `data/urls.txt`)
- `-m, --mode`: Concurrency preset - `auto|safe|aggressive` (default: `auto`)

**Environment variables:**
- `CTS_MAX_CONCURRENT_SITES`: Override max concurrent sites
- `CTS_MAX_CONCURRENT_PAGES`: Override max concurrent pages per site

### Extract Content
Extract content from URLs and convert to PDF:
```bash
# Basic usage
cts extract -i data/urls.txt -o output/

# With custom settings
cts extract -i data/urls.txt -o output/ -c 10 --timeout 60 -r 2
```

**Options:**
- `-i, --url-file`: Input file with URLs (one per line)
- `-o, --out-dir`: Output directory for PDFs (default: `output/`)
- `-c, --max-concurrency`: Maximum concurrent extractions (default: `6`)
- `--timeout`: Per-URL navigation timeout in seconds (default: `30`)
- `-r, --retries`: Number of retries on failure (default: `1`)

## Project Structure

```
ctis-to-sigma/
├── src/
│   ├── cli.py                    # Unified CLI entry point
│   ├── collector/                # URL collection module
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── commands.py           # Collection logic
│   │   └── link_collector.py    # Async URL crawler
│   └── extractor/                # Content extraction module
│       ├── __init__.py
│       ├── browser_async.py      # Async PDF rendering
│       ├── constants.py          # Configuration constants
│       ├── html.py               # HTML/CSS templates
│       ├── readability.py        # Content extraction helpers
│       ├── utils.py              # Utility functions
│       └── assets/
│           └── Readability.js    # Mozilla Readability library
├── pyproject.toml
└── README.md
```

## Commands Reference

| Command | Description |
|---------|-------------|
| `cts setup` | Check browser dependencies status |
| `cts setup --install` | Install Playwright and Chromium |
| `cts collect` | Collect CTI URLs from seed list |
| `cts extract` | Extract content and convert to PDF |

## Dependencies

- **Python**: >=3.12
- **typer**: CLI framework
- **rich**: Terminal formatting and progress display
- **playwright**: Browser automation (auto-installed via `cts setup --install`)