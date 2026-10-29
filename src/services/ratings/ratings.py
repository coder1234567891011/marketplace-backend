import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import Order, OrderStatus, Rating, RatingRole, User
from models.object_models.ratings import RatingCreate
from utils.errors import BadRequest, Conflict, NotFound
from utils.pagination import paginate


async def get_user_ratings(
    session: AsyncSession, user_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[Sequence[Rating], str | None]:
    return await paginate(session, select(Rating).where(Rating.ratee_id == user_id), Rating, limit, cursor)


async def get_rating(session: AsyncSession, rating_id: uuid.UUID) -> Rating:
    rating = await session.get(Rating, rating_id)
    if rating is None:
        raise NotFound("Rating not found")
    return rating


async def _refresh_rating_stats(session: AsyncSession, user_id: uuid.UUID, role: RatingRole) -> None:
    avg, count = (await session.execute(
        select(func.avg(Rating.rating), func.count(Rating.id)).where(Rating.ratee_id == user_id, Rating.role == role)
    )).one()
    # Lock the ratee's row so concurrent ratings can't overwrite each other's totals.
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    assert user is not None
    if role == RatingRole.seller:
        user.seller_rating_avg, user.seller_rating_count = avg, count
    else:
        user.buyer_rating_avg, user.buyer_rating_count = avg, count


async def create_rating(session: AsyncSession, current: CurrentUser, request: RatingCreate) -> Rating:
    order = await session.get(Order, request.order_id)
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    if order.status != OrderStatus.delivered:
        raise BadRequest("Orders can only be rated after delivery")

    if current.id == order.buyer_id:
        ratee_id, role = order.seller_id, RatingRole.seller
    else:
        ratee_id, role = order.buyer_id, RatingRole.buyer

    existing = await session.scalar(
        select(Rating.id).where(Rating.order_id == order.id, Rating.rater_id == current.id)
    )
    if existing is not None:
        raise Conflict("You already rated this order")

    rating = Rating(rater_id=current.id, ratee_id=ratee_id, role=role, **request.model_dump())
    session.add(rating)
    await session.flush()
    await _refresh_rating_stats(session, ratee_id, role)
    await session.flush()
    return rating
