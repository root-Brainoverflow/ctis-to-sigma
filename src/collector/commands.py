import os
import asyncio
from multiprocessing import cpu_count
from pathlib import Path
from typing import Literal

from rich import print

from .link_collector import LinkCollector


def collect_run(
    base_url_file: str,
    output_file: str,
    mode: Literal["auto", "safe", "aggressive"],
):
    """Read base_url_file and collect article/report links into output_file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    max_sites, max_pages = _decide_concurrency(mode)

    c = LinkCollector(base_url_file=base_url_file, output_file=output_file)
    c.max_concurrent_sites = max_sites
    c.max_concurrent_pages = max_pages

    print(f"[cyan]Concurrency:[/cyan] sites={max_sites} pages={max_pages} mode={mode}")
    asyncio.run(c.collect_links())


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