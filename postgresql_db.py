import streamlit as st
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    """Function to establish a connection to the Heroku PostgreSQL database."""
    DATABASE_URL = os.getenv("pulseai-db-url")  
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
            
            # Create indisponibility_kahraman table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_kahraman (
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

            # Create indisponibility_sunenergy table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_sunenergy (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')      

            # Create indisponibility_solarenergy table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_solarenergy (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')       

            # Create indisponibility_elnet table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_elnet (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_horeco table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_horeco (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_3dsteel table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_3d_steel (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_dragosel table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_dragosel (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_gess table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_gess (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_nrg table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_nrg (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_sun_grow_lucia table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_sun_grow_lucia (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_photovoltaic_energy_project table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_photovoltaic_energy_project (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_mm_mv table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_mm_mv (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_rosiori table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_rosiori (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_necaluxan table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_necaluxan (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(255) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    interval_from INT NOT NULL,
                    interval_to INT NOT NULL,
                    limitation_percentage FLOAT NOT NULL
                );
            ''')

            # Create indisponibility_adrem table

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indisponibility_adrem (
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