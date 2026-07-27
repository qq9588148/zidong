from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


BASE64_PATTERN = r"^[A-Za-z0-9+/]+={0,2}$"


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollmentChallengeRequest(StrictSchema):
    authorization_code: SecretStr = Field(min_length=40, max_length=100, repr=False)


class EnrollmentChallengeResponse(StrictSchema):
    challenge_id: UUID
    nonce: str = Field(min_length=44, max_length=44, pattern=BASE64_PATTERN)


class RegistrationRequest(StrictSchema):
    authorization_code: SecretStr = Field(min_length=40, max_length=100, repr=False)
    challenge_id: UUID
    username: str = Field(min_length=3, max_length=80)
    password: SecretStr = Field(min_length=12, max_length=128, repr=False)
    public_key_spki_der: str = Field(
        min_length=108, max_length=344, pattern=BASE64_PATTERN, repr=False
    )
    proof_der: str = Field(
        min_length=88, max_length=108, pattern=BASE64_PATTERN, repr=False
    )


class RebindRequest(RegistrationRequest):
    pass


class EnrollmentResponse(StrictSchema):
    account_id: UUID
    device_id: UUID
    public_key_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
