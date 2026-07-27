import tomllib
from pathlib import Path

import pytest

from champion_follow.cli import main


def test_pyproject_publishes_the_implemented_cli_entrypoint():
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    assert pyproject["project"]["scripts"]["champion-follow"] == (
        "champion_follow.cli:main"
    )
    assert callable(main)


def test_cli_exposes_process_ready_for_the_active_namespace(capsys):
    with pytest.raises(SystemExit) as error:
        main(["process-ready", "--help"])

    assert error.value.code == 0
    assert "--namespace-version" in capsys.readouterr().out


def test_app_readme_documents_windows_processing_and_privacy_boundaries():
    readme_path = Path(__file__).resolve().parents[2] / "README.md"

    assert readme_path.is_file()
    text = readme_path.read_text(encoding="utf-8")
    for marker in (
        "process-ready",
        "TEST_DATABASE_URL",
        "Windows",
        "Cookie",
        "Token",
        "actor_key",
    ):
        assert marker in text
