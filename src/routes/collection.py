import uuid

from fastapi import Depends, APIRouter

from app import get_current_user


router = APIRouter(
    prefix="/collections",
    tags=["collections"],
)

@router.get("/{user_id}")
async def get_user_collections(user_id: uuid.UUID = Depends(get_current_user)):
    return {"user_id": user_id, "collections": []}

@router.get("/{collection_id}")
async def get_collections(collection_id: uuid.UUID = Depends(get_current_user)):
    return {"collection_id": collection_id}

@router.post("/")
async def create_collection(collection_id: uuid.UUID = Depends(get_current_user)):
    return {"collection_id": collection_id}

@router.put("/{collection_id}")
async def update_collection(collection_id: uuid.UUID = Depends(get_current_user)):
    return {"collection_id": collection_id}

@router.delete("/{collection_id}")
async def delete_collection(collection_id: uuid.UUID = Depends(get_current_user)):
    return {"collection_id": collection_id}