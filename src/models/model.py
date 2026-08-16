from pydantic import BaseModel, ConfigDict


class _ORMBase(BaseModel):
    """Base class enabling creation directly from SQLAlchemy objects."""
    model_config = ConfigDict(from_attributes=True)