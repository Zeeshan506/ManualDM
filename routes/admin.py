from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_admin, require_sudo_admin, get_current_user
from models import User
from security import hash_password

router = APIRouter(prefix="/api/sudo", tags=["Admin Management"])


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    role: Literal["admin", "sales_rep", "sudo_admin"] = "sales_rep"


class UpdateRoleRequest(BaseModel):
    role: Literal["admin", "sales_rep", "sudo_admin"]


class UpdateNameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class UpdatePasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


class UpdateStatusRequest(BaseModel):
    is_active: bool


@router.post("/users")
def create_user(
    body: CreateUserRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """
    Create a new user account.
    - Admins can create: admin, sales_rep
    - Sudo admins can create: admin, sales_rep, sudo_admin
    """
    # Only admins and sudo_admins can create users
    if current_user.role not in {"admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and sudo admins can create users",
        )

    # Admins cannot create sudo_admins
    if current_user.role == "admin" and body.role == "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sudo admins can create other sudo admins",
        )

    # Check if username already exists
    existing_user = db.query(User).filter(User.username == body.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Create new user
    new_user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
        is_active=True,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "role": new_user.role,
        "is_active": new_user.is_active,
    }


@router.get("/users")
def list_users(
    current_user: Annotated[User, Depends(require_admin)],
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    """
    List all staff users (admin, sudo_admin, and sales_rep).
    Only accessible to admins and sudo admins.
    """
    query = db.query(User).filter(User.role.in_(["admin", "sudo_admin", "sales_rep"]))
    if active_only:
        query = query.filter(User.is_active.is_(True))

    users = query.order_by(User.role.asc(), User.id.asc()).all()

    return [
        {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "is_current_user": user.id == current_user.id,
        }
        for user in users
    ]


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    body: UpdateRoleRequest,
    current_user: Annotated[User, Depends(require_sudo_admin)],
    db: Session = Depends(get_db),
):
    """
    Change the role of a user.
    Only sudo admins can change roles.
    Cannot change the role of another sudo admin.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target_user.role == "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the role of another sudo admin",
        )

    target_user.role = body.role
    db.commit()
    db.refresh(target_user)

    return {
        "id": target_user.id,
        "username": target_user.username,
        "role": target_user.role,
        "is_active": target_user.is_active,
    }


@router.patch("/users/{user_id}/name")
def update_user_name(
    user_id: int,
    body: UpdateNameRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    """
    Update a user's name/display name.
    Admins can update names of sales_rep users.
    Sudo admins can update names of any non-sudo_admin user.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Admins can only update sales_rep names
    if current_user.role == "admin" and target_user.role != "sales_rep":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only update sales rep names",
        )

    # Sudo admins cannot update other sudo admin names
    if current_user.role == "sudo_admin" and target_user.role == "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update another sudo admin's name",
        )

    target_user.name = body.name
    db.commit()
    db.refresh(target_user)

    return {
        "id": target_user.id,
        "username": target_user.username,
        "name": target_user.name,
        "role": target_user.role,
    }


@router.patch("/users/{user_id}/password")
def update_user_password(
    user_id: int,
    body: UpdatePasswordRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    """
    Update a user's password.
    Users can only update their own password.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Users can only change their own password
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only change your own password",
        )

    target_user.hashed_password = hash_password(body.password)
    db.commit()
    db.refresh(target_user)

    return {
        "id": target_user.id,
        "username": target_user.username,
        "role": target_user.role,
        "is_active": target_user.is_active,
        "password_updated": True,
    }


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    body: UpdateStatusRequest,
    current_user: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    """
    Activate or deactivate a user account.
    Admins can deactivate sales_rep users.
    Sudo admins can deactivate any non-sudo_admin user.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Admins can only update sales_rep status
    if current_user.role == "admin" and target_user.role != "sales_rep":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only update sales rep status",
        )

    # Sudo admins cannot update other sudo admin status
    if current_user.role == "sudo_admin" and target_user.role == "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update another sudo admin's status",
        )

    target_user.is_active = body.is_active
    db.commit()
    db.refresh(target_user)

    return {
        "id": target_user.id,
        "username": target_user.username,
        "role": target_user.role,
        "is_active": target_user.is_active,
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    """
    Delete a user account.
    - Admins can delete lower-ranking admins and sales reps (not other admins at same level or sudo_admin)
    - Sudo admins can delete any admin or sales_rep (not other sudo_admins)
    - Cannot delete the current user's own account
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Cannot delete yourself
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # Regular admins cannot delete sudo_admins
    if current_user.role == "admin" and target_user.role in {"admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only delete sales rep accounts",
        )

    # Sudo admins cannot delete other sudo_admins
    if current_user.role == "sudo_admin" and target_user.role == "sudo_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete another sudo admin account",
        )

    db.delete(target_user)
    db.commit()