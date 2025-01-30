import streamlit as st
import pandas as pd
import psycopg2
from datetime import date, timedelta

# =============================================================== Rendering the Indisponibility database for Solina =========================================================
def get_connection():
    """Get a connection to the PostgreSQL database."""
    return psycopg2.connect(
            host="localhost",  # Replace with your database server's address if remote
            database="Indisponibility",  # Database name
            user="streamlit",  # Your PostgreSQL user
            password="streamlit"  # Your PostgreSQL password
        )

def create_table():
    """Create the indisponibility table if it does not exist."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS indisponibility_Solina (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            Interval_from INT NOT NULL,
            Interval_to INT NOT NULL,
            limitation_percentage REAL NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def add_indisponibility_to_db(limitation_type, start_date, end_date, interval_from, interval_to, percentage):
    """Add indisponibility data to the database."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO indisponibility_Solina (type, start_date, end_date, interval_from, interval_to, limitation_percentage)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (limitation_type, start_date, end_date, interval_from, interval_to, percentage))
    conn.commit()
    conn.close()

def delete_indisponibility_from_db(entry_id):
    """Delete indisponibility data from the database."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        DELETE FROM indisponibility_Solina WHERE id = %s
    ''', (entry_id,))
    conn.commit()
    conn.close()

def render_indisponibility_db_Solina():
    """Render and manage the indisponibility database for Solina client."""
    create_table()

    # Widget for Grid Limitation
    st.subheader("Grid Limitation")
    grid_start_date = st.date_input("Grid Limitation Start Date", value=date.today())
    grid_end_date = st.date_input("Grid Limitation End Date", value=date.today())
    grid_limitation_percentage = st.number_input("Grid Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    grid_interval_from = st.number_input("Interval From (Grid)", min_value=0, max_value=24, value=1)
    grid_interval_to = st.number_input("Interval To (Grid)", min_value=0, max_value=24, value=24)

    if st.button("Add Grid Limitation"):
        add_indisponibility_to_db("Grid Limitation", grid_start_date, grid_end_date, grid_interval_from, grid_interval_to, grid_limitation_percentage)
        st.success("Grid Limitation added successfully!")

    # Widget for Asset Limitation
    st.subheader("Asset Limitation")
    asset_start_date = st.date_input("Asset Limitation Start Date", value=date.today())
    asset_end_date = st.date_input("Asset Limitation End Date", value=date.today())
    asset_limitation_percentage = st.number_input("Asset Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    asset_interval_from = st.number_input("Interval From (Asset)", min_value=0, max_value=24, value=1)
    asset_interval_to = st.number_input("Interval To (Asset)", min_value=0, max_value=24, value=24)

    if st.button("Add Asset Limitation"):
        add_indisponibility_to_db("Asset Limitation", asset_start_date, asset_end_date, asset_interval_from, asset_interval_to, asset_limitation_percentage)
        st.success("Asset Limitation added successfully!")

    # Retrieve and display the stored indisponibility data from the database
    conn = get_connection()
    indisponibility_df = pd.read_sql_query("SELECT * FROM indisponibility_Solina", conn)
    st.subheader("Indisponibility Data")
    st.dataframe(indisponibility_df)
    conn.close()

    # Widget to select and remove an entry
    st.subheader("Remove an Entry")
    entry_id_to_delete = st.selectbox("Select Entry ID to Delete", indisponibility_df['id'])

    if st.button("Delete Selected Entry"):
        delete_indisponibility_from_db(entry_id_to_delete)
        st.success(f"Entry ID {entry_id_to_delete} deleted successfully!")
        st.experimental_rerun()  # Rerun to refresh the displayed data

    # Calculate tomorrow's date
    tomorrow = date.today() + timedelta(days=1)

    # Query the database for any indisponibility scheduled for tomorrow
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT Interval_from, Interval_to, limitation_percentage 
        FROM indisponibility_Solina 
        WHERE start_date <= %s AND end_date >= %s
    ''', (tomorrow, tomorrow))

    # Fetch the result
    result = c.fetchone()
    conn.close()

    # Assign the values to variables if there is an indisponibility for tomorrow
    if result:
        interval_from, interval_to, limitation_percentage = result
        return interval_from, interval_to, limitation_percentage

    return None

# Repeat similar changes for Astro and Imperial sections
def render_indisponibility_db_Astro():
    conn = get_connection()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS indisponibility_Astro (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            Interval_from INT NOT NULL,
            Interval_to INT NOT NULL,
            limitation_percentage REAL NOT NULL
        )
    ''')
    conn.commit()

    def add_indisponibility_to_db(limitation_type, start_date, end_date, interval_from, interval_to, percentage):
        c.execute('''
            INSERT INTO indisponibility_Astro (type, start_date, end_date, interval_from, interval_to, limitation_percentage)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (limitation_type, start_date, end_date, interval_from, interval_to, percentage))
        conn.commit()

    def delete_indisponibility_from_db(entry_id):
        c.execute('''
            DELETE FROM indisponibility_Astro WHERE id = %s
        ''', (entry_id,))
        conn.commit()

    # The rest of the function is similar to the Solina version.
    # Just replace indisponibility_Solina with indisponibility_Astro in all SQL queries.

    st.subheader("Grid Limitation")
    grid_start_date = st.date_input("Grid Limitation Start Date", value=date.today())
    grid_end_date = st.date_input("Grid Limitation End Date", value=date.today())
    grid_limitation_percentage = st.number_input("Grid Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    grid_interval_from = st.number_input("Interval From (Grid)", min_value=0, max_value=24, value=1)
    grid_interval_to = st.number_input("Interval To (Grid)", min_value=0, max_value=24, value=24)

    if st.button("Add Grid Limitation"):
        add_indisponibility_to_db("Grid Limitation", grid_start_date, grid_end_date, grid_interval_from, grid_interval_to, grid_limitation_percentage)
        st.success("Grid Limitation added successfully!")

    st.subheader("Asset Limitation")
    asset_start_date = st.date_input("Asset Limitation Start Date", value=date.today())
    asset_end_date = st.date_input("Asset Limitation End Date", value=date.today())
    asset_limitation_percentage = st.number_input("Asset Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    asset_interval_from = st.number_input("Interval From (Asset)", min_value=0, max_value=24, value=1)
    asset_interval_to = st.number_input("Interval To (Asset)", min_value=0, max_value=24, value=24)

    if st.button("Add Asset Limitation"):
        add_indisponibility_to_db("Asset Limitation", asset_start_date, asset_end_date, asset_interval_from, asset_interval_to, asset_limitation_percentage)
        st.success("Asset Limitation added successfully!")

    indisponibility_df = pd.read_sql_query("SELECT * FROM indisponibility_Astro", conn)
    st.subheader("Indisponibility Data")
    st.dataframe(indisponibility_df)
    conn.close()

    st.subheader("Remove an Entry")
    entry_id_to_delete = st.selectbox("Select Entry ID to Delete", indisponibility_df['id'])

    if st.button("Delete Selected Entry"):
        delete_indisponibility_from_db(entry_id_to_delete)
        st.success(f"Entry ID {entry_id_to_delete} deleted successfully!")
        st.experimental_rerun()

    tomorrow = date.today() + timedelta(days=1)
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT Interval_from, Interval_to, limitation_percentage 
        FROM indisponibility_Astro 
        WHERE start_date <= %s AND end_date >= %s
    ''', (tomorrow, tomorrow))

    result = c.fetchone()
    conn.close()

    if result:
        interval_from, interval_to, limitation_percentage = result
        return interval_from, interval_to, limitation_percentage

    return None

# Similar conversion needed for the Imperial database as well.

def render_indisponibility_db_Imperial():
    conn = get_connection()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS indisponibility_Imperial (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            Interval_from INT NOT NULL,
            Interval_to INT NOT NULL,
            limitation_percentage REAL NOT NULL
        )
    ''')
    conn.commit()

    def add_indisponibility_to_db(limitation_type, start_date, end_date, interval_from, interval_to, percentage):
        c.execute('''
            INSERT INTO indisponibility_Imperial (type, start_date, end_date, interval_from, interval_to, limitation_percentage)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (limitation_type, start_date, end_date, interval_from, interval_to, percentage))
        conn.commit()

    def delete_indisponibility_from_db(entry_id):
        c.execute('''
            DELETE FROM indisponibility_Imperial WHERE id = %s
        ''', (entry_id,))
        conn.commit()

    st.subheader("Grid Limitation")
    grid_start_date = st.date_input("Grid Limitation Start Date", value=date.today())
    grid_end_date = st.date_input("Grid Limitation End Date", value=date.today())
    grid_limitation_percentage = st.number_input("Grid Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    grid_interval_from = st.number_input("Interval From (Grid)", min_value=0, max_value=24, value=1)
    grid_interval_to = st.number_input("Interval To (Grid)", min_value=0, max_value=24, value=24)

    if st.button("Add Grid Limitation"):
        add_indisponibility_to_db("Grid Limitation", grid_start_date, grid_end_date, grid_interval_from, grid_interval_to, grid_limitation_percentage)
        st.success("Grid Limitation added successfully!")

    st.subheader("Asset Limitation")
    asset_start_date = st.date_input("Asset Limitation Start Date", value=date.today())
    asset_end_date = st.date_input("Asset Limitation End Date", value=date.today())
    asset_limitation_percentage = st.number_input("Asset Limitation Percentage", min_value=0.0, max_value=100.0, value=50.0)
    asset_interval_from = st.number_input("Interval From (Asset)", min_value=0, max_value=24, value=1)
    asset_interval_to = st.number_input("Interval To (Asset)", min_value=0, max_value=24, value=24)

    if st.button("Add Asset Limitation"):
        add_indisponibility_to_db("Asset Limitation", asset_start_date, asset_end_date, asset_interval_from, asset_interval_to, asset_limitation_percentage)
        st.success("Asset Limitation added successfully!")

    indisponibility_df = pd.read_sql_query("SELECT * FROM indisponibility_Imperial", conn)
    st.subheader("Indisponibility Data")
    st.dataframe(indisponibility_df)
    conn.close()

    st.subheader("Remove an Entry")
    entry_id_to_delete = st.selectbox("Select Entry ID to Delete", indisponibility_df['id'])

    if st.button("Delete Selected Entry"):
        delete_indisponibility_from_db(entry_id_to_delete)
        st.success(f"Entry ID {entry_id_to_delete} deleted successfully!")
        st.experimental_rerun()

    tomorrow = date.today() + timedelta(days=1)
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT Interval_from, Interval_to, limitation_percentage 
        FROM indisponibility_Imperial 
        WHERE start_date <= %s AND end_date >= %s
    ''', (tomorrow, tomorrow))

    result = c.fetchone()
    conn.close()

    if result:
        interval_from, interval_to, limitation_percentage = result
        return interval_from, interval_to, limitation_percentage

    return None
