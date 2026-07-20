#!/usr/bin/env python3
"""Main entry point."""

import click

@click.command()
@click.option("--name", default="World", help="Name to greet")
def main(name: str):
    """Main function."""
    click.echo(f"Hello, {name}!")

if __name__ == "__main__":
    main()
