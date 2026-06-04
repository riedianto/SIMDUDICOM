from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # SIMRS SQL Server Configuration
    SIMRS_SQLSERVER_HOST: str = Field(default="103.167.236.130")
    SIMRS_SQLSERVER_PORT: int = Field(default=1433)
    SIMRS_SQLSERVER_DB: str = Field(default="artha_medika")
    SIMRS_SQLSERVER_USER: str = Field(default="sa")
    SIMRS_SQLSERVER_PASSWORD: str = Field(default="secret_password")

    # Local Bridge SQL Server Configuration
    BRIDGE_SQLSERVER_HOST: str = Field(default="bridge-db")
    BRIDGE_SQLSERVER_PORT: int = Field(default=1433)
    BRIDGE_SQLSERVER_DB: str = Field(default="RadiologyBridge")
    BRIDGE_SQLSERVER_USER: str = Field(default="sa")
    BRIDGE_SQLSERVER_PASSWORD: str = Field(default="Bridge_Password123!")

    # Redis Config
    REDIS_HOST: str = Field(default="redis")
    REDIS_PORT: int = Field(default=6379)

    # JWT Config
    JWT_SECRET: str = Field(default="supersecretjwtkeyforradiologybridge")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # SATUSEHAT Credentials
    SATUSEHAT_BASE_URL: str = Field(default="https://api-sandbox.kemkes.go.id/fhir-r4/v1")
    SATUSEHAT_CLIENT_ID: str = Field(default="your_client_id")
    SATUSEHAT_CLIENT_SECRET: str = Field(default="your_client_secret")
    SATUSEHAT_DICOM_BASE_URL: str = Field(default="https://api-sandbox.kemkes.go.id")
    SATUSEHAT_ORGANIZATION_ID: str = Field(default="")
    SATUSEHAT_AUTH_URL: str = Field(default="https://api-sandbox.kemkes.go.id/oauth2/v1/accesstoken?grant_type=client_credentials")

    # Webhook Callback ke SIMRS (Panduan Kemenkes Hal. 8-9)
    WEBHOOK_URL: str = Field(default="")
    WEBHOOK_USER: str = Field(default="")
    WEBHOOK_PASSWORD: str = Field(default="")

    # DICOM AE Titles
    MWL_AE_TITLE: str = Field(default="SIMDUDIM")
    STORAGE_AE_TITLE: str = Field(default="SIMDUDIM_STORE")
    LOCAL_DICOM_STORAGE_PATH: str = Field(default="./storage/dicom")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
