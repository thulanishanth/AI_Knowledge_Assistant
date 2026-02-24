from sqlalchemy import Column, Integer, String, Numeric, Date, ForeignKey
from sqlalchemy.orm import relationship
from app.database.connection import Base

class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    room_number = Column(Integer, nullable=False, unique=True)
    room_type = Column(String(50), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    status = Column(String(20), default="available")

    # Establish a one-to-many relationship with bookings
    bookings = relationship("Booking", back_populates="room")

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_name = Column(String(100), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    check_in = Column(Date, nullable=False)
    check_out = Column(Date, nullable=False)

    # Map the relationships back to rooms and forward to payments
    room = relationship("Room", back_populates="bookings")
    payments = relationship("Payment", back_populates="booking")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"))
    amount = Column(Numeric(10, 2), nullable=False)
    payment_date = Column(Date, nullable=False)

    # Map the relationship back to the booking
    booking = relationship("Booking", back_populates="payments")