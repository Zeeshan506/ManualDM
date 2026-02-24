from typing import Annotated

from fastapi import APIRouter, Depends

from dependencies import get_current_user
from models import User


router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("/me")
def get_me(current_user: Annotated[User, Depends(get_current_user)]):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "role": current_user.role,
    }
