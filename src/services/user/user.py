from datetime import datetime

from models.database import User
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('users')

async def db_get_user(user_id) -> User:
    user = table.select("*").eq('id',user_id).execute()
    return User(**user.data[0])

async def db_create_user(user_id, request) -> User:
    new_user = request.dict(exclude_unset=True)
    new_user['id'] = user_id["sub"]
    new_user["created_at"] = datetime.utcnow().isoformat()
    user = table.insert(new_user).execute()
    return User(**user.data[0])

async def db_update_user(user_id, request) -> User:
    update_user = request.dict(exclude_unset=True)
    user = table.update(update_user).eq("id", user_id["sub"]).execute()
    return User(**user.data[0])

async def db_delete_user(user_id) -> None:
    user = table.delete().eq("id", user_id).execute()
    return None