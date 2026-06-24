"""
Entry point for `python -m ovbuilder`.

Delegates to the Typer CLI defined in main.py.
"""

from .main import cli

if __name__ == "__main__":
    cli()
