# src/extractor/browser_async.py
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright, Browser, BrowserContext
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

from .constants import DEFAULT_TIMEOUT_S
from .utils import read_url_lines, sha256_hex

console = Console()


async def launch_browser() -> tuple[Browser, BrowserContext]:
    """Launch a headless Chromium browser and return (browser, context)."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context()
    return browser, ctx


async def close_browser(browser: Browser, ctx: BrowserContext) -> None:
    """Close the browser context and browser."""
    await ctx.close()
    await browser.close()


def sanitize_filename(title: str) -> str:
    """Convert title to safe filename."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)
    sanitized = re.sub(r'\s+', '_', sanitized.strip())
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    return sanitized if sanitized else "untitled"


async def get_page_title(ctx: BrowserContext, url: str) -> str:
    """Extract page title."""
    try:
        page = await ctx.new_page()
        await page.goto(url, timeout=30000)
        title = await page.title()
        await page.close()
        return title.strip() if title else "untitled"
    except Exception:
        return "untitled"


async def render_url_to_pdf_async(
    ctx: BrowserContext,
    url: str,
    out_path: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> None:
    """Render a URL to PDF using Playwright."""
    page = await ctx.new_page()
    try:
        await page.goto(url, timeout=timeout_s * 1000, wait_until="networkidle")
        await page.pdf(path=str(out_path), format="A4")
    finally:
        await page.close()


async def _worker(
    sem: asyncio.Semaphore,
    ctx: BrowserContext,
    url: str,
    out_dir: Path,
    timeout_s: int,
    retries: int,
    echo: Callable[[str], None],
    event_q: asyncio.Queue,
) -> None:
    """Worker task that processes a single URL with retries and concurrency control."""
    attempt = 0
    temp_pdf_path = out_dir / f"temp_{sha256_hex(url)}.pdf"

    while True:
        attempt += 1
        async with sem:
            try:
                # Generate PDF
                await render_url_to_pdf_async(ctx, url, temp_pdf_path, timeout_s)

                # Extract page title
                title = await get_page_title(ctx, url)
                safe_title = sanitize_filename(title)

                # Generate final filename (prevent duplicates)
                final_pdf_path = out_dir / f"{safe_title}.pdf"
                counter = 1
                while final_pdf_path.exists():
                    final_pdf_path = out_dir / f"{safe_title}_{counter}.pdf"
                    counter += 1

                # Rename temp file to final filename
                os.rename(temp_pdf_path, final_pdf_path)

                await event_q.put(("ok", url, str(final_pdf_path.name)))
                return
            except Exception as exc:
                if temp_pdf_path.exists():
                    temp_pdf_path.unlink()

                if attempt <= retries + 1:
                    await asyncio.sleep(min(2 * attempt, 5))
                else:
                    await event_q.put(("fail", url, str(exc)))
                    return


async def extract_run(
    url_file: Path,
    out_dir: Path,
    timeout_s: int,
    max_concurrency: int,
    retries: int,
) -> None:
    """Main async runner for extraction."""
    urls = read_url_lines(url_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    browser, ctx = await launch_browser()
    try:
        sem = asyncio.Semaphore(max(1, max_concurrency))

        total = len(urls)
        processed_lines: list[str] = []

        progress = Progress(
            TextColumn("[bold]Overall[/bold]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            MofNCompleteColumn(),
            TextColumn("processed"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn(" ETA "),
            TimeRemainingColumn(),
            expand=True,
        )
        task_id = progress.add_task("render", total=total)

        def _render_ui():
            h = console.size.height
            reserved_rows = 7
            tail_cap = max(3, h - reserved_rows)

            over = max(0, len(processed_lines) - tail_cap)
            tail = processed_lines[-tail_cap:] if processed_lines else []
            if over > 0:
                head = f"[dim]… {over} older processed entries hidden …[/dim]"
                body_lines = [head, *tail]
            else:
                body_lines = tail

            body = (
                "\n".join(body_lines)
                if body_lines
                else "[dim]No URLs processed yet...[/dim]"
            )

            return Group(
                Panel(
                    body,
                    title="Processed (latest at bottom)",
                    border_style="green",
                    padding=(0, 1),
                ),
                progress,
            )

        event_q: asyncio.Queue = asyncio.Queue()

        tasks = []
        for url in urls:
            tasks.append(
                _worker(
                    sem, ctx, url, out_dir, timeout_s, retries, print, event_q
                )
            )

        async def ui_loop() -> None:
            completed = 0

            try:
                with Live(_render_ui(), refresh_per_second=8, transient=False) as live:
                    while completed < total:
                        status, url, result = await event_q.get()
                        completed += 1
                        progress.update(task_id, advance=1)

                        if status == "ok":
                            processed_lines.append(f"[green]✓[/green] {url}")
                        else:
                            processed_lines.append(
                                f"[red]✗[/red] {url}  [dim]{result}[/dim]"
                            )

                        live.update(_render_ui())
            finally:
                pass

        await asyncio.gather(asyncio.create_task(ui_loop()), *tasks)

    finally:
        await close_browser(browser, ctx)