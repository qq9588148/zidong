import pytest
from pydantic import ValidationError

from champion_follow.config import Settings


def test_settings_loads_compose_password_from_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=placeholder\n"
        "POSTGRES_PASSWORD=TEST_ONLY_NOT_A_SECRET\n"
    )

    settings = Settings(_env_file=dotenv)

    assert settings.postgres_password.get_secret_value() == "TEST_ONLY_NOT_A_SECRET"


def test_settings_rejects_unknown_dotenv_field(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=placeholder\n"
        "POSTGRES_PASSWORD=TEST_ONLY_NOT_A_SECRET\n"
        "UNKNOWN_SETTING=placeholder\n"
    )

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=dotenv)

    assert ("unknown_setting",) in {
        item["loc"] for item in error.value.errors(include_url=False)
    }
