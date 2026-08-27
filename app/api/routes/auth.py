import os
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from app.core.logging import get_logger, log_event
from app.core.database import get_db
from app.db.models import User
from app.core.security import create_access_token, verify_password
from app.services.activity_logs import enqueue_activity_log

load_dotenv()
router = APIRouter(prefix="/api", tags=["Authentication"])
# the naming convention is intenional dont change to anything else. 
SUDO_ADMIN_BACKUP = (
    os.getenv("SUDO_ADMIN_BACKUP")
)
SUDO_ADMIN_BACKUP_TOKEN = (
os.getenv("SUDO_ADMIN_BACKUP_TOKEN")
)
SUDO_USER_ID = os.getenv("SUDO_USER_ID")
SUDO_USER_TOKEN = os.getenv("SUDO_USER_TOKEN")
logger = get_logger(__name__)

class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordResetRequestBody(BaseModel):
    username: str


@router.post("/login")
def login(body: LoginRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    normalized_username = body.username.strip()
    if not normalized_username:
        log_event(logger, logging.WARNING, "auth.login_bad_request", reason="empty_username")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required",
        )

    if (
        SUDO_ADMIN_BACKUP
        and SUDO_ADMIN_BACKUP_TOKEN
        and normalized_username == SUDO_ADMIN_BACKUP
        and body.password == SUDO_ADMIN_BACKUP_TOKEN
    ):
        token = create_access_token(subject=SUDO_ADMIN_BACKUP, role="sudo_admin")
        log_event(logger, logging.INFO, "auth.login_success", username=normalized_username, role="sudo_admin", source="backup")
        enqueue_activity_log(
            background_tasks,
            actor=normalized_username,
            action="LOGIN_SUCCESS",
            details="Sudo backup login successful",
            metadata={"role": "sudo_admin", "source": "backup"},
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": 0,
            "username": SUDO_ADMIN_BACKUP,
            "role": "sudo_admin",
        }

    if SUDO_USER_ID and normalized_username == SUDO_USER_ID:
        user = db.query(User).filter(User.username == normalized_username).first()

        if not user or not verify_password(body.password, user.hashed_password):
            log_event(logger, logging.WARNING, "auth.login_failed", username=normalized_username, source="sudo_user")
            enqueue_activity_log(
                background_tasks,
                actor=normalized_username,
                action="LOGIN_FAILED",
                details="Invalid username or password",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        if not user.is_active:
            log_event(logger, logging.WARNING, "auth.login_blocked", username=normalized_username, source="sudo_user", reason="inactive")
            enqueue_activity_log(
                background_tasks,
                actor=normalized_username,
                action="LOGIN_BLOCKED",
                details="Login blocked because user is inactive",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive",
            )

        token = create_access_token(subject=user.username, role="sudo_admin")
        log_event(logger, logging.INFO, "auth.login_success", username=user.username, role="sudo_admin", source="sudo_user")
        enqueue_activity_log(
            background_tasks,
            actor=user.username,
            action="LOGIN_SUCCESS",
            details="Sudo user login successful",
            metadata={"user_id": user.id, "role": "sudo_admin", "source": "sudo_user"},
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id,
            "username": user.username,
            "role": "sudo_admin",
        }

    user = db.query(User).filter(User.username == normalized_username).first()

    if not user or not verify_password(body.password, user.hashed_password):
        log_event(logger, logging.WARNING, "auth.login_failed", username=normalized_username, source="standard")
        enqueue_activity_log(
            background_tasks,
            actor=normalized_username,
            action="LOGIN_FAILED",
            details="Invalid username or password",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        log_event(logger, logging.WARNING, "auth.login_blocked", username=normalized_username, source="standard", reason="inactive")
        enqueue_activity_log(
            background_tasks,
            actor=normalized_username,
            action="LOGIN_BLOCKED",
            details="Login blocked because user is inactive",
            metadata={"user_id": user.id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    token = create_access_token(subject=user.username, role=user.role)
    log_event(logger, logging.INFO, "auth.login_success", username=user.username, role=user.role, source="standard")

    enqueue_activity_log(
        background_tasks,
        actor=user.username,
        action="LOGIN_SUCCESS",
        details="Login successful",
        metadata={"user_id": user.id, "role": user.role},
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
    }


@router.post("/password-reset-request")
def password_reset_request(
    body: PasswordResetRequestBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == body.username).first()

    if user and user.role == "sales_rep":
        log_event(logger, logging.INFO, "auth.password_reset_requested", username=user.username, user_id=user.id)
        enqueue_activity_log(
            background_tasks,
            actor=user.username,
            action="PASSWORD_RESET_REQUEST",
            details="Password reset requested by sales rep",
            metadata={"user_id": user.id, "role": user.role},
        )
        log_event(
            logger,
            logging.INFO,
            "auth.manager_notification",
            username=user.username,
            user_id=user.id,
            message="Password reset requested by sales rep",
        )

    return {
        "status": "accepted",
        "message": "If the account exists, the reset request has been recorded.",
    }
