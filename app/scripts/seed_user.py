from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.db.models import User
from passlib.context import CryptContext
import os

# Setup the password hasher (make sure this matches your security.py later)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password):
    return pwd_context.hash(password)

def seed_users():
    db: Session = SessionLocal()
    
    try:
        # 1. Create Admin User
        admin_user = User(
            username="BabarKhan",
            hashed_password=get_password_hash("Admin123"),
            role="admin",
            is_active=True
        )
        
        # 2. Create Random Sales Rep
        sales_rep = User(
            username="AliSales",
            hashed_password=get_password_hash("Sales123"),
            role="sales_rep",
            is_active=True
        )
        
        db.add(admin_user)
        db.add(sales_rep)

        ceo_username = os.getenv("CEO_USERNAME", "")
        ceo_password = os.getenv("CEO_PASSWORD", "")
        if ceo_username and ceo_password:
            ceo_exists = db.query(User).filter(User.username == ceo_username).first()
            if not ceo_exists:
                ceo_user = User(
                    username=ceo_username,
                    hashed_password=get_password_hash(ceo_password),
                    role="super_admin",
                    is_active=True,
                )
                db.add(ceo_user)

        db.commit()
        
        print("Successfully created users:")
        print("1. Admin: BabarKhan / Admin123")
        print("2. Sales Rep: AliSales / Sales123")
        if ceo_username and ceo_password:
            print(f"3. Super Admin (CEO): {ceo_username} / [from CEO_PASSWORD env]")
        
    except Exception as e:
        db.rollback()
        print(f"Error creating users: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_users()