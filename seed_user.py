from sqlalchemy.orm import Session
from database import SessionLocal
from models import User
from passlib.context import CryptContext

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
        db.commit()
        
        print("Successfully created users:")
        print("1. Admin: BabarKhan / Admin123")
        print("2. Sales Rep: AliSales / Sales123")
        
    except Exception as e:
        db.rollback()
        print(f"Error creating users: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_users()