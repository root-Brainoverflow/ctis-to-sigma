import typer
from .commands import collect_run

app = typer.Typer()

@app.command()
def main(
    base_url_file: str = "base_url.txt",
    output_file: str = "data/urls.txt",
    mode: str = "auto",
):
    """Direct execution of collector"""
    collect_run(base_url_file, output_file, mode)

if __name__ == "__main__":
    app()