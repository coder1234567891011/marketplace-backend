import uuid

from fastapi import Depends, APIRouter

from app import get_current_user

router = APIRouter(
    prefix="/addresses",
    tags=["addresses"],
)

@router.get("/{user_id}")
async def get_user_addresses(user_id: uuid.UUID = Depends(get_current_user)):
    return {"user_id": user_id, "addresses": []}

@router.get("/{address_id}")
async def get_address(address_id: uuid.UUID = Depends(get_current_user)):
    return {"address_id": address_id}

@router.post("/")
async def create_address(address_id: uuid.UUID = Depends(get_current_user)):
    return {"address_id": address_id}

@router.put("/{address_id}")
async def update_address(address_id: uuid.UUID = Depends(get_current_user)):
    return {"address_id": address_id}

@router.delete("/{address_id}")
async def delete_address(address_id: uuid.UUID = Depends(get_current_user)):
    return {"address_id": address_id}
