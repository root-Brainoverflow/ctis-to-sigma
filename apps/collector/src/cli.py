import os
import asyncio
import subprocess
from multiprocessing import cpu_count
from pathlib import Path
from typing import Optional, Literal

import typer
from rich import print

from link_collector import LinkCollector

app = typer.Typer(add_completion=False, no_args_is_help=True)
collect = typer.Typer(help="CTI link collection")
app.add_typer(collect, name="collect")


@collect.command("run")
def collect_run(
    base_url_file: Path = typer.Option(
        "base_url.txt", "--base-url-file", "-b", exists=True, help="Seed URL list file"
    ),
    output_file: Path = typer.Option(
        "data/urls.txt", "--out-file", "-o", help="Output file for collected article/report URLs"
    ),
    mode: Literal["auto", "safe", "aggressive"] = typer.Option(
        "auto", "--mode", "-m", help="Concurrency preset: auto | safe | aggressive"
    ),
):
    """Read base_url_file and collect article/report links into output_file."""
    output_file.parent.mkdir(parents=True, exist_ok=True)

    max_sites, max_pages = _decide_concurrency(mode)

    c = LinkCollector(base_url_file=str(base_url_file), output_file=str(output_file))
    c.max_concurrent_sites = max_sites
    c.max_concurrent_pages = max_pages

    print(f"[concurrency] sites={max_sites} pages={max_pages} mode={mode}")
    asyncio.run(c.collect_links())


@collect.command("setup")
def collect_setup(
    install: Optional[bool] = typer.Option(
        False, "--install", help="Install Playwright Chromium if missing"
    ),
):
    """Check Playwright runtime availability and install if needed."""
    ok = True
    try:
        subprocess.run(
            ["python", "-m", "playwright", "install", "--dry-run"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        ok = False

    print(f"playwright: {'ok' if ok else 'missing'}")
    if not ok and install:
        print("installing chromium...")
        subprocess.run(
            ["python", "-m", "playwright", "install", "--with-deps", "chromium"],
            check=True,
        )
        print("done.")


def _decide_concurrency(mode: str) -> tuple[int, int]:
    """Resolve concurrency from preset mode with optional env overrides."""
    # defaults by mode
    if mode == "safe":
        sites, pages = 2, 3
    elif mode == "aggressive":
        sites, pages = 6, 8
    else:
        # auto
        cpu = max(1, cpu_count())
        sites = min(3, max(1, cpu // 2))
        pages = 5

    # env overrides
    env_sites = os.getenv("CTS_MAX_CONCURRENT_SITES")
    env_pages = os.getenv("CTS_MAX_CONCURRENT_PAGES")
    if env_sites and env_sites.isdigit():
        sites = max(1, int(env_sites))
    if env_pages and env_pages.isdigit():
        pages = max(1, int(env_pages))
    return sites, pages