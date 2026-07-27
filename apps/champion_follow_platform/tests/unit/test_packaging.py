import tomllib
from pathlib import Path

from champion_follow.cli import main


def test_pyproject_publishes_the_implemented_cli_entrypoint():
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    assert pyproject["project"]["scripts"]["champion-follow"] == (
        "champion_follow.cli:main"
    )
    assert callable(main)
