from pydantic import BaseModel
from core.enums import UserRole

class InvitationBase(BaseModel):
    mobile_number: str
class InvitationCreate(InvitationBase):
    pass
class Invitation(InvitationBase):
    id: int
    token: str
    is_used: bool
    class Config:
        from_attributes = True

class UserBase(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str
class User(UserBase):
    id: int
    role: UserRole
    class Config:
        from_attributes = True