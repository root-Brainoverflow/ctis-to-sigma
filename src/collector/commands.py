import os
import asyncio
from multiprocessing import cpu_count
from pathlib import Path
from typing import Literal

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TaskProgressColumn,
    MofNCompleteColumn,
)

from .link_collector import LinkCollector

console = Console()


def collect_run(
    base_url_file: str,
    output_file: str,
    mode: Literal["auto", "safe", "aggressive"],
):
    """Read base_url_file and collect article/report links into output_file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    max_sites, max_pages = _decide_concurrency(mode)

    console.print(f"[cyan]Concurrency:[/cyan] sites={max_sites} pages={max_pages} mode={mode}")
    
    asyncio.run(_collect_with_ui(base_url_file, output_file, max_sites, max_pages))


async def _collect_with_ui(
    base_url_file: str,
    output_file: str,
    max_sites: int,
    max_pages: int,
):
    """Run collection with Rich UI."""
    # Read seed URLs
    with open(base_url_file) as f:
        seed_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    total_sites = len(seed_urls)
    processed_lines: list[str] = []

    progress = Progress(
        TextColumn("[bold]Collecting[/bold]"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        MofNCompleteColumn(),
        TextColumn("sites"),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn(" ETA "),
        TimeRemainingColumn(),
        expand=True,
    )
    task_id = progress.add_task("collect", total=total_sites)

    def _render_ui():
        h = console.size.height
        reserved_rows = 7
        tail_cap = max(3, h - reserved_rows)

        over = max(0, len(processed_lines) - tail_cap)
        tail = processed_lines[-tail_cap:] if processed_lines else []
        
        if over > 0:
            head = f"[dim]… {over} older entries hidden …[/dim]"
            body_lines = [head, *tail]
        else:
            body_lines = tail

        body = (
            "\n".join(body_lines)
            if body_lines
            else "[dim]No sites processed yet...[/dim]"
        )

        return Group(
            Panel(
                body,
                title="Processed Sites (latest at bottom)",
                border_style="cyan",
                padding=(0, 1),
            ),
            progress,
        )

    # Event queue for progress updates
    event_q: asyncio.Queue = asyncio.Queue()

    # Setup collector with event callbacks
    collector = LinkCollector(base_url_file=base_url_file, output_file=output_file)
    collector.max_concurrent_sites = max_sites
    collector.max_concurrent_pages = max_pages

    async def on_site_start(site_url: str):
        """Called when site processing starts."""
        pass  # No action needed, just tracking

    async def on_site_complete(site_url: str, links_count: int, success: bool):
        """Called when site processing completes."""
        await event_q.put(("ok" if success else "fail", site_url, links_count))

    collector.on_site_start = on_site_start
    collector.on_site_complete = on_site_complete

    # UI update loop
    async def ui_loop():
        """UI update loop."""
        completed = 0

        with Live(_render_ui(), refresh_per_second=8, transient=False) as live:
            while completed < total_sites:
                status, site_url, count = await event_q.get()
                completed += 1
                progress.update(task_id, advance=1)

                if status == "ok":
                    processed_lines.append(
                        f"[green]✓[/green] {site_url} [dim]({count} links)[/dim]"
                    )
                else:
                    processed_lines.append(
                        f"[red]✗[/red] {site_url} [dim](failed)[/dim]"
                    )

                live.update(_render_ui())

    # Run collector and UI together
    await asyncio.gather(
        collector.collect_links(),
        ui_loop()
    )

    # Final summary
    console.print(f"\n[green]✓[/green] Saved to: {Path(output_file).absolute()}")
    console.print(f"[green]✓[/green] Total links: {len(collector.collected_links)}")


def _decide_concurrency(mode: str) -> tuple[int, int]:
    """Resolve concurrency from preset mode with optional env overrides."""
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