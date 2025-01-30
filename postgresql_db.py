import streamlit as st
import psycopg2
import os

def get_connection():
    """Function to establish a connection to the Heroku PostgreSQL database."""
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://u6vgnrtb422bju:p593a702cae233b84c4a2a29ad9f8f13116fa3a407ea6b2ed91d97280a5e17d9c@c8lj070d5ubs83.cluster-czrs8kj4isg7.us-east-1.rds.amazonaws.com:5432/d67n79bcvb33fq")  
    try:
        # Enforce SSL mode for Heroku PostgreSQL
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        st.error(f"Error connecting to the database: {e}")
        return None

def create_indisponibility_tables():
    conn = get_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            
            # Create indisponibility_solina table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_solina (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')
            
            # Create indisponibility_astro table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_astro (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')
            
            # Create indisponibility_imperial table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_imperial (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')
            
            conn.commit()
            cursor.close()
            conn.close()
            st.success("Tables created successfully in the database!")
        except Exception as e:
            st.error(f"Error creating tables: {e}")
    else:
        st.error("Failed to connect to the database.")

def list_tables():
    conn = get_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            
            # Query to list tables
            cursor.execute('''
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public';
            ''')
            
            tables = cursor.fetchall()
            cursor.close()
            conn.close()

            if tables:
                st.write("Tables in the database:")
                for table in tables:
                    st.write(table[0])
            else:
                st.write("No tables found in the database.")
        except Exception as e:
            st.error(f"Error listing tables: {e}")
    else:
        st.error("Failed to connect to the database.")

def main():
    st.title("Test Heroku PostgreSQL Connection")

    conn = get_connection()

    if conn:
        st.success("Successfully connected to the Heroku database!")
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT current_database();")
            db_name = cursor.fetchone()[0]
            st.write(f"Connected to the database: {db_name}")

            cursor.close()
            conn.close()
        except Exception as e:
            st.error(f"Error executing query: {e}")
    else:
        st.error("Failed to connect to the database.")

    if st.button("Create Indisponibility Tables"):
        create_indisponibility_tables()

    if st.button("List Tables"):
        list_tables()

if __name__ == "__main__":
    main()