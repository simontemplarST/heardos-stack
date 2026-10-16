"""Entry point for the PyInstaller build: `HeardOS` opens the GUI, `HeardOS cli ...` runs the headless commands."""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from heardos_installer.__main__ import cli
        cli(sys.argv[2:])
    else:
        from heardos_installer.app import main
        main()
