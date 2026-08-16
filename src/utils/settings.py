from pydantic.v1 import BaseSettings


class Settings(BaseSettings):
    supabase_project_ref: str = ""
    supabase_api_key: str = ""

settings = Settings()