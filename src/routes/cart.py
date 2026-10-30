import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.cart import CartItemSet, CartRead
from services.cart.cart import clear_cart, get_cart, remove_cart_item, set_cart_item
from services.persistence.database import SessionDep
from utils import limits

router = APIRouter(
    prefix="/cart",
    tags=["cart"],
)


@router.get("")
async def read_cart(current: AuthUser, session: SessionDep) -> CartRead:
    """The cart grouped by seller, with any lines that can't be bought as they are."""
    return await get_cart(session, current)


@router.put("/items/{listing_id}", dependencies=[limits.CART])
async def put_cart_item(listing_id: uuid.UUID, request: CartItemSet, current: AuthUser,
                        session: SessionDep) -> CartRead:
    """Add a listing to the cart, or set how many copies of it."""
    await set_cart_item(session, current, listing_id, request.quantity)
    return await get_cart(session, current)


@router.delete("/items/{listing_id}", dependencies=[limits.CART])
async def delete_cart_item(listing_id: uuid.UUID, current: AuthUser, session: SessionDep) -> CartRead:
    await remove_cart_item(session, current, listing_id)
    return await get_cart(session, current)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.CART])
async def empty_cart(current: AuthUser, session: SessionDep) -> None:
    await clear_cart(session, current)
