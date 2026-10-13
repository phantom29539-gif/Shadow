import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    SPREADSHEET_ID: str = os.getenv(
        "SPREADSHEET_ID",
        "",
    )

    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))

    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "40"))

    BOT_PROXY: str = os.getenv("BOT_PROXY", "").strip()

    @property
    def spreadsheet_url(self) -> str:
        return (
            "https://docs.google.com/spreadsheets/d/"
            f"{self.SPREADSHEET_ID}/export?format=xlsx"
        )


settings = Settings()