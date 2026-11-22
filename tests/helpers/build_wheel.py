"""Build the current generated project with its declared Poetry backend."""

from poetry.core.masonry.api import build_wheel

if __name__ == "__main__":
    build_wheel("dist")
