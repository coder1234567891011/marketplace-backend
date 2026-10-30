import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.listings import ListingRead
from models.object_models.wants import (
    OptimizeRequest,
    OptimizeResult,
    WantItemCreate,
    WantItemRead,
    WantItemStatus,
    WantItemUpdate,
    WantsImport,
    WantsImportResult,
    WantsListCreate,
    WantsListRead,
)
from services.persistence.database import SessionDep
from services.wants import wants
from utils import limits

router = APIRouter(
    prefix="/wants",
    tags=["wants"],
)


@router.get("")
async def list_wants_lists(current: AuthUser, session: SessionDep) -> list[WantsListRead]:
    """The caller's wants lists, newest first."""
    return [WantsListRead.model_validate(wl).model_copy(update={"item_count": n})
            for wl, n in await wants.get_wants_lists(session, current)]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.WANTS])
async def create_wants_list(request: WantsListCreate, current: AuthUser, session: SessionDep) -> WantsListRead:
    return WantsListRead.model_validate(await wants.create_list(session, current, request))


@router.patch("/{list_id}", dependencies=[limits.WANTS])
async def rename_wants_list(list_id: uuid.UUID, request: WantsListCreate, current: AuthUser,
                            session: SessionDep) -> WantsListRead:
    return WantsListRead.model_validate(await wants.rename_list(session, current, list_id, request))


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.WANTS])
async def delete_wants_list(list_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await wants.delete_list(session, current, list_id)


@router.get("/{list_id}/items")
async def list_items(list_id: uuid.UUID, current: AuthUser, session: SessionDep) -> list[WantItemStatus]:
    """Each item with the copies the caller owns and what's for sale."""
    return await wants.list_status(session, current, list_id)


@router.post("/{list_id}/items", status_code=status.HTTP_201_CREATED, dependencies=[limits.WANTS])
async def add_item(list_id: uuid.UUID, request: WantItemCreate, current: AuthUser,
                   session: SessionDep) -> WantItemRead:
    return WantItemRead.model_validate(await wants.add_item(session, current, list_id, request))


@router.post("/{list_id}/import", dependencies=[limits.WANTS])
async def import_decklist(list_id: uuid.UUID, request: WantsImport, current: AuthUser,
                          session: SessionDep) -> WantsImportResult:
    """Add the cards in a decklist."""
    return await wants.import_items(session, current, list_id, request)


@router.get("/{list_id}/alerts")
async def price_alerts(list_id: uuid.UUID, current: AuthUser, session: SessionDep) -> list[ListingRead]:
    """Listings at or under the target price (max_price_cents) of an item."""
    return await wants.price_alerts(session, current, list_id)


@router.post("/{list_id}/optimize", dependencies=[limits.WANTS_OPTIMIZE])
async def optimize(list_id: uuid.UUID, request: OptimizeRequest, current: AuthUser,
                   session: SessionDep) -> OptimizeResult:
    """The cheapest way found to buy the list, cards plus shipping, optionally added to the cart."""
    return await wants.optimize_list(session, current, list_id, request)


@router.patch("/items/{item_id}", dependencies=[limits.WANTS])
async def update_item(item_id: uuid.UUID, request: WantItemUpdate, current: AuthUser,
                      session: SessionDep) -> WantItemRead:
    return WantItemRead.model_validate(await wants.update_item(session, current, item_id, request))


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.WANTS])
async def delete_item(item_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await wants.delete_item(session, current, item_id)
