from pydantic.v1 import BaseSettings


class Settings(BaseSettings):
    supabase_project_ref: str = ""