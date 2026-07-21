"""
``python -m ovbuilder`` entry point.

Delegates entirely to the Typer app in main.py so there is one CLI surface
whether invoked as a console_script or as a module.
"""

from .main import cli

if __name__ == "__main__":
    # Typer's __call__ runs the CLI (sys.argv).
    cli()
