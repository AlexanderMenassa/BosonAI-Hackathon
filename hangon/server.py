"""Compatibility entry point for the browser server."""

from .browser.server import Hub, Room, demo_number, ice_configuration, main, run, static_page


if __name__ == "__main__":
    main()
