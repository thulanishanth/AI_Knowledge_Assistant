"""
Database schema definitions for AI Knowledge Assistant.
This can be used for RAG context or reference in prompts.
"""

# Example: Users table
USERS_TABLE = """
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    email VARCHAR(100) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Example: Orders table
ORDERS_TABLE = """
CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT,
    product VARCHAR(100),
    quantity INT,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# Combine all schema definitions
SCHEMA_DEFINITION = f"{USERS_TABLE}\n{ORDERS_TABLE}"
