from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class _ORMBase(BaseModel):
    """Base class enabling creation directly from SQLAlchemy objects."""
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """A page of results. Pass next_cursor back as ?cursor= to get the next page."""
    items: list[T]
    next_cursor: str | None = None
