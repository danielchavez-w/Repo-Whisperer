"""Enable `python -m repo_whisperer <command>` as the package entry point."""

from repo_whisperer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
