import typer


app = typer.Typer(help="Resume & JD analysis CLI")


@app.callback()
def main() -> None:
    """Root callback reserved for global options."""
    return


if __name__ == "__main__":
    app()

