import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import date, timedelta

# Google Sheets URL
url = "https://docs.google.com/spreadsheets/d/1zTx9eJV67sNHxUEGvIQiMOR7LarLZWqmsVi3hfZz1gw/edit?usp=sharing"

# Create a connection object
conn = st.connection("gsheets", type=GSheetsConnection)

# Load data from the specified sheet
def load_data(sheet_name):
    data = conn.read(spreadsheet=url, worksheet=sheet_name)
    df = pd.DataFrame(data)
    return df

# Save the DataFrame back to the specified sheet
def save_data(sheet_name, df):
    conn.write(df, spreadsheet=url, sheet=sheet_name)

# Add a new indisponibility entry to the database
def add_indisponibility_to_gsheets(sheet_name, limitation_type, start_date, end_date, interval_from, interval_to, percentage):
    df = load_data(sheet_name)
    
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
        "Interval_from": [interval_from],
        "Interval_to": [interval_to],
        "limitation_percentage": [percentage]
    })
    
    df = pd.concat([df, new_entry], ignore_index=True)
    save_data(sheet_name, df)
    st.success(f"New {limitation_type} entry added successfully!")

# Check for indisponibilities scheduled for tomorrow and return relevant values
def check_tomorrow_indisponibilities(sheet_name):
    df = load_data(sheet_name)
    
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
        interval_from, interval_to, limitation_percentage = upcoming.iloc[0][['Interval_from', 'Interval_to', 'limitation_percentage']]
        st.warning(f"Indisponibility found for tomorrow: Interval from {interval_from} to {interval_to}, Limitation percentage: {limitation_percentage}%")
    return interval_from, interval_to, limitation_percentage

# Render and manage the indisponibility database for the given client
def render_indisponibility_db(sheet_name, title):
    # Add new Grid Limitation
    st.subheader(f"{title} - Add Grid Limitation")
    grid_start_date = st.date_input(f"Grid Limitation Start Date ({title})", value=date.today())
    grid_end_date = st.date_input(f"Grid Limitation End Date ({title})", value=date.today())
    grid_limitation_percentage = st.number_input(f"Grid Limitation Percentage ({title})", min_value=0.0, max_value=100.0, value=50.0)
    grid_interval_from = st.number_input(f"Interval From (Grid) ({title})", min_value=0, max_value=24, value=1)
    grid_interval_to = st.number_input(f"Interval To (Grid)", min_value=0, max_value=24, value=24)

    if st.button(f"Add Grid Limitation ({title})"):
        add_indisponibility_to_excel(sheet_name, "Grid Limitation", grid_start_date, grid_end_date, grid_interval_from, grid_interval_to, grid_limitation_percentage)

    # Add new Asset Limitation
    st.subheader(f"{title} - Add Asset Limitation")
    asset_start_date = st.date_input(f"Asset Limitation Start Date ({title})", value=date.today())
    asset_end_date = st.date_input(f"Asset Limitation End Date ({title})", value=date.today())
    asset_limitation_percentage = st.number_input(f"Asset Limitation Percentage ({title})", min_value=0.0, max_value=100.0, value=50.0)
    asset_interval_from = st.number_input(f"Interval From (Asset)", min_value=0, max_value=24, value=1)
    asset_interval_to = st.number_input(f"Interval To (Asset)", min_value=0, max_value=24, value=24)

    if st.button(f"Add Asset Limitation ({title})"):
        add_indisponibility_to_excel(sheet_name, "Asset Limitation", asset_start_date, asset_end_date, asset_interval_from, asset_interval_to, asset_limitation_percentage)

    # Loading the indisponibilities for the selected Client
    df = load_data(sheet_name)
    st.write(f"Loaded Data for {title}")
    st.dataframe(df)

    st.subheader(f"Remove an Entry ({title})")
    entry_id_to_delete = st.selectbox(f"Select Entry ID to Delete ({title})", df['id'].tolist())
    st.write(f"Selected ID to delete: {entry_id_to_delete}")

    # Delete the selected entry
    if st.button(f"Delete Selected Entry ({title})"):
        st.write("DataFrame before deletion:")
        st.write(df)
        
        df = df[df['id'] != entry_id_to_delete]
        st.write("DataFrame after deletion:")
        st.write(df)

        save_data(sheet_name, df)

        df_reloaded = load_data(sheet_name)
        st.write("DataFrame after reloading from Excel:")
        st.write(df_reloaded)

        if entry_id_to_delete not in df_reloaded['id'].values:
            st.success(f"Entry ID {entry_id_to_delete} was successfully removed from the Excel file.")
        else:
            st.error(f"Entry ID {entry_id_to_delete} was NOT removed from the Excel file.")
        
        time.sleep(3)  # Delay for 3 seconds
        st.rerun()

    # Check for tomorrow's indisponibilities
    interval_from, interval_to, limitation_percentage = check_tomorrow_indisponibilities(sheet_name)
    return interval_from, interval_to, limitation_percentage

# Specific functions for each client
def render_indisponibility_db_Solina():
    return render_indisponibility_db("indisponibility_Solina", "Solina")

def render_indisponibility_db_Astro():
    return render_indisponibility_db("indisponibility_Astro", "Astro")

def render_indisponibility_db_Imperial():
    return render_indisponibility_db("indisponibility_Imperial", "Imperial")