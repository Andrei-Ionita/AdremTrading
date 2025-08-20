import streamlit as st
import psycopg2
import pandas as pd
from datetime import date, timedelta
import os
import uuid
from dotenv import load_dotenv

load_dotenv()
# Heroku PostgreSQL connection details
DB_URL = os.getenv("pulseai-db-url")
# Establish a connection to the PostgreSQL database
def get_connection():
    try:
        conn = psycopg2.connect(DB_URL, sslmode='require')
        return conn
    except Exception as e:
        st.error(f"Error connecting to the database: {e}")
        return None

# Load data from the PostgreSQL table
def load_data(table_name):
    conn = get_connection()
    if conn:
        query = f"SELECT * FROM {table_name};"
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    else:
        return pd.DataFrame()

def save_data(sheet_name, df):
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure consistent column names (all lowercase)
    df.columns = df.columns.str.lower()

    # Drop any duplicate columns
    df = df.loc[:, ~df.columns.duplicated()]

    # Check again to ensure no duplicate columns remain
    if df.columns.duplicated().any():
        st.error(f"Duplicate column names found: {df.columns[df.columns.duplicated()].tolist()}")
        return  # Exit if there are still duplicate columns

    # Debugging: Print the DataFrame to check before insertion
    st.write("Inserting row:", df.tail(1))  # Show last added row

    # Replace NaN values in 'interval_from' and 'interval_to' with default values
    df['interval_from'] = df['interval_from'].fillna(0)
    df['interval_to'] = df['interval_to'].fillna(0)

    # Ensure all columns needed for the SQL insert are present
    try:
        cursor.execute(
            '''
            INSERT INTO public.{0} (id, type, start_date, end_date, interval_from, interval_to, limitation_percentage)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            '''.format(sheet_name),
            (
                int(df['id'].iloc[-1]),
                df['type'].iloc[-1],
                df['start_date'].iloc[-1],
                df['end_date'].iloc[-1],
                int(df['interval_from'].iloc[-1]),  # Cast to integer
                int(df['interval_to'].iloc[-1]),    # Cast to integer
                float(df['limitation_percentage'].iloc[-1])
            )
        )
        conn.commit()
        cursor.close()
        conn.close()
        st.success(f"New {df['type'].iloc[-1]} entry added successfully!")
    except Exception as e:
        st.error(f"Error inserting row {df.iloc[-1].to_dict()}: {e}")

# Add a new indisponibility entry to the database
def add_indisponibility_to_postgres(table_name, limitation_type, start_date, end_date, interval_from, interval_to, percentage):
    df = load_data(table_name)
    
    # Generate a new ID (assuming IDs are integers and incrementing by 1)
    if df.empty:
        new_id = 1
    else:
        new_id = df['id'].max() + 1

    new_entry = pd.DataFrame({
        "id": [new_id],
        "type": [limitation_type],
        "start_date": [start_date],
        "end_date": [end_date],
        "interval_from": [interval_from],
        "interval_to": [interval_to],
        "limitation_percentage": [percentage]
    })

    df = pd.concat([df, new_entry], ignore_index=True)
    
    # st.write(f"Inserting row: id={new_id}, type={limitation_type}, start_date={start_date}, end_date={end_date}, "
             # f"interval_from={interval_from}, interval_to={interval_to}, limitation_percentage={percentage}")

    save_data(table_name, df)
    # st.success(f"New {limitation_type} entry added successfully!")

# Check for indisponibilities scheduled for tomorrow and return relevant values
def check_tomorrow_indisponibilities(table_name):
    df = load_data(table_name)

    # Debugging: Print the columns in the DataFrame
    # st.write("Columns in the DataFrame:", df.columns)

    # Convert start_date and end_date to datetime objects
    df['start_date'] = pd.to_datetime(df['start_date']).dt.date
    df['end_date'] = pd.to_datetime(df['end_date']).dt.date

    # Calculate tomorrow's date
    tomorrow = date.today() + timedelta(days=1)

    # Filter for indisponibilities that are active tomorrow
    upcoming = df[(df['start_date'] <= tomorrow) & (df['end_date'] >= tomorrow)]

    # Initialize the variables with default values
    interval_from = interval_to = limitation_percentage = None

    if not upcoming.empty:
        try:
            # Ensure that the column names exist in the DataFrame
            interval_from, interval_to, limitation_percentage = upcoming.iloc[0][['interval_from', 'interval_to', 'limitation_percentage']]
            st.warning(f"Indisponibility found for tomorrow: Interval from {interval_from} to {interval_to}, Limitation percentage: {limitation_percentage}%")
        except KeyError as e:
            st.error(f"Column names error: {e}. Please check the column names in the database.")
    else:
        st.write("No indisponibility scheduled for tomorrow.")

    return interval_from, interval_to, limitation_percentage

# Render and manage the indisponibility database for the given client
def render_indisponibility_db(table_name, title):
    def generate_key(prefix):
        # Generate a consistent key with a prefix for each widget using table name and title
        return f"{prefix}_{table_name}_{title}"

    # Add new Grid Limitation
    st.subheader(f"{title} - Add Grid Limitation")
    grid_start_date = st.date_input(f"Grid Limitation Start Date ({title})", value=date.today(), key=generate_key("grid_start_date"))
    grid_end_date = st.date_input(f"Grid Limitation End Date ({title})", value=date.today(), key=generate_key("grid_end_date"))
    grid_limitation_percentage = st.number_input(f"Grid Limitation Percentage ({title})", min_value=0.0, max_value=100.0, value=50.0, key=generate_key("grid_limitation_percentage"))

    # Cast to integers and add debug prints
    grid_interval_from = int(st.number_input(f"Interval From (Grid) ({title})", min_value=0, max_value=24, value=1, key=generate_key("grid_interval_from")))
    grid_interval_to = int(st.number_input(f"Interval To (Grid)", min_value=0, max_value=24, value=24, key=generate_key("grid_interval_to")))

    if st.button(f"Add Grid Limitation ({title})", key=generate_key("add_grid_limitation")):
        add_indisponibility_to_postgres(table_name, "Grid Limitation", grid_start_date, grid_end_date, grid_interval_from, grid_interval_to, grid_limitation_percentage)

    # Add new Asset Limitation
    st.subheader(f"{title} - Add Asset Limitation")
    asset_start_date = st.date_input(f"Asset Limitation Start Date ({title})", value=date.today(), key=generate_key("asset_start_date"))
    asset_end_date = st.date_input(f"Asset Limitation End Date ({title})", value=date.today(), key=generate_key("asset_end_date"))
    asset_limitation_percentage = st.number_input(f"Asset Limitation Percentage ({title})", min_value=0.0, max_value=100.0, value=50.0, key=generate_key("asset_limitation_percentage"))

    # Cast to integers and add debug prints
    asset_interval_from = int(st.number_input(f"Interval From (Asset)", min_value=0, max_value=24, value=1, key=generate_key("asset_interval_from")))
    asset_interval_to = int(st.number_input(f"Interval To (Asset)", min_value=0, max_value=24, value=24, key=generate_key("asset_interval_to")))

    if st.button(f"Add Asset Limitation ({title})", key=generate_key("add_asset_limitation")):
        add_indisponibility_to_postgres(table_name, "Asset Limitation", asset_start_date, asset_end_date, asset_interval_from, asset_interval_to, asset_limitation_percentage)

    # Loading the indisponibilities for the selected Client
    df = load_data(table_name)
    st.write(f"Loaded Data for {title}")
    st.dataframe(df)

    st.subheader(f"Remove an Entry ({title})")

    # Selecting the ID to delete
    entry_id_to_delete = st.selectbox(f"Select Entry ID to Delete ({title})", df['id'].tolist(), key=generate_key("entry_id_to_delete"))
    st.write(f"Selected ID to delete: {entry_id_to_delete}")

    # Deleting the selected entry from the database
    if st.button(f"Delete Selected Entry ({title})", key=generate_key("delete_entry")):
        st.write("Data before deletion:")
        st.write(df)

        # Connect to the database and delete the row with the selected ID
        try:
            conn = get_connection()  # You should have a function like this to connect to your PostgreSQL DB
            cursor = conn.cursor()

            # Execute the delete command for the specific row
            cursor.execute(f"DELETE FROM {table_name} WHERE id = %s", (entry_id_to_delete,))
            conn.commit()

            st.success(f"Entry ID {entry_id_to_delete} was successfully removed from the database.")

            # Reload the updated data
            df_reloaded = load_data(table_name)
            st.write("Data after reloading from Postgres:")
            st.write(df_reloaded)

            if entry_id_to_delete not in df_reloaded['id'].values:
                st.success(f"Entry ID {entry_id_to_delete} was successfully removed.")
            else:
                st.error(f"Entry ID {entry_id_to_delete} was NOT removed.")

            # Close the connection
            cursor.close()
            conn.close()

            # Rerun the app to reflect the changes
            st.rerun()

        except Exception as e:
            st.error(f"Error while deleting entry: {e}")

    # Check for tomorrow's indisponibilities
    interval_from, interval_to, limitation_percentage = check_tomorrow_indisponibilities(table_name)
    return interval_from, interval_to, limitation_percentage

# Specific functions for each client
# def render_indisponibility_db_Solina():
#     return render_indisponibility_db("indisponibility_solina", "Solina")

def render_indisponibility_db_Astro():
    return render_indisponibility_db("indisponibility_astro", "Astro")

def render_indisponibility_db_Imperial():
    return render_indisponibility_db("indisponibility_imperial", "Imperial")

# def render_indisponibility_db_RES_Energy():
#     return render_indisponibility_db("indisponibility_res", "RES Energy")

# def render_indisponibility_db_Luxus():
#     return render_indisponibility_db("indisponibility_luxus", "Luxus")

# def render_indisponibility_db_Kek_Hal():
#     return render_indisponibility_db("indisponibility_luxus", "Kek_Hal")

def render_indisponibility_db_Kahraman():
    return render_indisponibility_db("indisponibility_kahraman", "Kahraman")

def render_indisponibility_db_SunEnergy():
    return render_indisponibility_db("indisponibility_sunenergy", "SunEnergy")

def render_indisponibility_db_SolarEnergy():
    return render_indisponibility_db("indisponibility_solarenergy", "SolarEnergy")

def render_indisponibility_db_Elnet():
    return render_indisponibility_db("indisponibility_elnet", "Elnet")

def render_indisponibility_db_Horeco():
    return render_indisponibility_db("indisponibility_horeco", "Horeco")

def render_indisponibility_db_3D_Steel():
    return render_indisponibility_db("indisponibility_3d_steel", "3D Steel")