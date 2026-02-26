import os

from typing import Annotated
from dotenv import load_dotenv

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from database import get_db
from models import User
from security import decode_access_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")
load_dotenv()
SUDO_ADMIN_BACKUP = (
    os.getenv("SUDO_ADMIN_BACKUP")
)
SUDO_USER_ID = os.getenv("SUDO_USER_ID")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if not payload:
        raise credentials_exception

    username = payload.get("sub")
    role = payload.get("role")
    if not username:
        raise credentials_exception

    if username == SUDO_ADMIN_BACKUP and role == "sudo_admin":
        local_sudo_admin = User()
        local_sudo_admin.id = 0
        local_sudo_admin.username = SUDO_ADMIN_BACKUP
        local_sudo_admin.role = "sudo_admin"
        local_sudo_admin.is_active = True
        return local_sudo_admin

    if username == SUDO_USER_ID and role == "sudo_admin":
        local_sudo_admin = User()
        local_sudo_admin.id = 0
        local_sudo_admin.username = SUDO_USER_ID
        local_sudo_admin.role = "sudo_admin"
        local_sudo_admin.is_active = True
        return local_sudo_admin

    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise credentials_exception

    return user


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role not in {"admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_sudo_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sudo admin access required",
        )
    return current_user
