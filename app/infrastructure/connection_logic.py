
import os
import pandas as pd
import random
from faker import Faker
from sqlalchemy import create_engine
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

fake = Faker()

rows = []

for i in range(35000):
    order_date = fake.date_between(start_date='-1y', end_date='today')
    delivery_date = order_date + timedelta(days=random.randint(1, 7))

    price = round(random.uniform(100, 5000), 2)
    quantity = random.randint(1, 5)
    discount = round(random.uniform(0, 0.3), 2)

    rows.append({
        "order_id": f"O{i+1}",
        "customer_name": fake.name(),
        "customer_city": fake.city(),
        "product_name": random.choice(["Laptop", "Phone", "Tablet", "Headphones", "Camera"]),
        "category": random.choice(["Electronics", "Accessories"]),
        "price": price,
        "quantity": quantity,
        "order_date": order_date,
        "delivery_date": delivery_date,
        "payment_method": random.choice(["UPI", "Card", "Cash", "Net Banking"]),
        "order_status": random.choice(["Delivered", "Pending", "Canceled"]),
        "discount": discount,
        "rating": round(random.uniform(1, 5), 1),
        "is_returned": random.choice([0, 1]),
        "shipping_cost": round(random.uniform(20, 200), 2)
    })

db_user = os.getenv("DB_USER", "root")
db_pass = os.getenv("DB_PASSWORD", "1234")
db_host = os.getenv("DB_HOST", "localhost")
db_name = os.getenv("DB_NAME", "DB_NAME")

df = pd.DataFrame(rows)

# Connect to MySQL
engine = create_engine(f"mysql+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}")

# Insert into table
df.to_sql("customer_orders", con=engine, if_exists="append", index=False)

print("✅ 35,000 rows inserted successfully!")