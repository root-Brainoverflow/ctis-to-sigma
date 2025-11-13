import subprocess
import typer
from pathlib import Path
from rich import print

from src.collector.commands import collect_run
from src.extractor.browser_async import extract_run

cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.command("setup")
def setup(
    install: bool = typer.Option(
        False, "--install", help="Install all dependencies (Playwright, etc.)"
    ),
):
    """Check and install dependencies for collector, extractor and parser."""
    # Playwright 체크
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

    print(f"[cyan]Playwright:[/cyan] {'Already installed' if ok else 'missing'}")
    
    if not ok and install:
        print("[yellow]Installing Playwright chromium...[/yellow]")
        subprocess.run(
            ["python", "-m", "playwright", "install", "--with-deps", "chromium"],
            check=True,
        )
        print("[green]Playwright installed.[/green]")
    
    if install and ok:
        print("[green]All dependencies are already installed.[/green]")


@cli.command("collect")
def collect(
    base_url_file: str = typer.Option(
        "base_url.txt", "--base-url-file", "-b", help="Seed URL list file"
    ),
    output_file: str = typer.Option(
        "data/urls.txt", "--out-file", "-o", help="Output file for collected URLs"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Concurrency preset: auto | safe | aggressive"
    ),
):
    """Collect CTI article/report links."""
    collect_run(base_url_file, output_file, mode)


@cli.command("extract")
def extract(
    url_file: Path = typer.Option(
        ..., "--url-file", "-i", help="Text file with one URL per line"
    ),
    out_dir: Path = typer.Option(
        Path("output"), "--out-dir", "-o", help="Output directory"
    ),
    timeout_s: int = typer.Option(
        30, help="Per-URL navigation timeout (seconds)"
    ),
    max_concurrency: int = typer.Option(
        6, "--max-concurrency", "-c", help="Max concurrent pages"
    ),
    retries: int = typer.Option(
        1, "--retries", "-r", help="Retries per URL on failure"
    ),
):
    """Extract content from URLs and save as PDFs."""
    import asyncio
    asyncio.run(extract_run(url_file, out_dir, timeout_s, max_concurrency, retries))


@cli.command("parse")
def parse(
    input_file: str = typer.Option(
        "data/urls.txt", "--input", "-i", help="Input file with URLs"
    ),
):
    """Parse CTI content from collected URLs."""
    print(f"[yellow]Parser not implemented yet.[/yellow]")
    print(f"[cyan]Input file:[/cyan] {input_file}")


if __name__ == '__main__':
    cli()