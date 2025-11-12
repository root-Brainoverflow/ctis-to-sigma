import subprocess
import typer
from typing import Optional
from rich import print

from src.collector.commands import collect_run
# from src.parser.commands import parse_run  # parser 구현 시 주석 해제

cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.command("setup")
def setup(
    install: bool = typer.Option(
        False, "--install", help="Install all dependencies (Playwright, etc.)"
    ),
):
    """Check and install dependencies for collector and parser."""
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
    
    # TODO: Parser 관련 의존성 체크 추가
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


@cli.command("parse")
def parse(
    input_file: str = typer.Option(
        "data/urls.txt", "--input", "-i", help="Input file with URLs"
    ),
):
    """Parse CTI content from collected URLs."""
    # parse_run(input_file)  # parser 구현 시 주석 해제
    print(f"[yellow]Parser not implemented yet.[/yellow]")
    print(f"[cyan]Input file:[/cyan] {input_file}")


if __name__ == '__main__':
    cli()