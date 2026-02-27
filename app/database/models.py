#app/database/models.py
"""Database models for the Kaggle Hotel Reservations dataset."""
from sqlalchemy import Column, Integer, String, Numeric
from app.database.connection import Base

# pylint: disable=too-few-public-methods
class HotelReservation(Base):
    """Model representing the hotel_reservations table."""
    __tablename__ = "hotel_reservations"

    booking_id = Column(String(255), primary_key=True, index=True)
    no_of_adults = Column(Integer)
    no_of_children = Column(Integer)
    no_of_weekend_nights = Column(Integer)
    no_of_week_nights = Column(Integer)
    type_of_meal_plan = Column(String(255))
    required_car_parking_space = Column(Integer)
    room_type_reserved = Column(String(255))
    lead_time = Column(Integer)
    arrival_year = Column(Integer)
    arrival_month = Column(Integer)
    arrival_date = Column(Integer)
    market_segment_type = Column(String(255))
    repeated_guest = Column(Integer)
    no_of_previous_cancellations = Column(Integer)
    no_of_previous_bookings_not_canceled = Column(Integer)
    avg_price_per_room = Column(Numeric(10, 2))
    no_of_special_requests = Column(Integer)
    booking_status = Column(String(255))