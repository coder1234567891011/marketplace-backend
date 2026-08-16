from supabase import create_client, Client
from utils.settings import settings


class SupabaseClient:
    url: str = f"https://{settings.supabase_project_ref}.supabase.co/"
    key: str = settings.supabase_api_key
    supabase_client: Client = create_client(url, key)