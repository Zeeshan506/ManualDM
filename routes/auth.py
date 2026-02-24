from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User
from security import create_access_token, verify_password


router = APIRouter(prefix="/api", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordResetRequestBody(BaseModel):
    username: str


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    normalized_username = body.username.strip()
    if not normalized_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required",
        )

    user = db.query(User).filter(User.username == normalized_username).first()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    token = create_access_token(subject=user.username, role=user.role)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
    }


@router.post("/password-reset-request")
def password_reset_request(body: PasswordResetRequestBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()

    if user and user.role == "sales_rep":
        print(
            f"MANAGER_NOTIFICATION: Password reset requested by sales rep username={user.username} user_id={user.id}"
        )

    return {
        "status": "accepted",
        "message": "If the account exists, the reset request has been recorded.",
    }
