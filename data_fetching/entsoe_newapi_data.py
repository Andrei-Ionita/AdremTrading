import streamlit as st
import zipfile
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta
import requests
import os
from dotenv import load_dotenv
import numpy as np
import wapi
import joblib
import xlsxwriter
import matplotlib.pyplot as plt
import pytz
# import plotly.express as px

# Load environment variables for ENTSO-E API key
load_dotenv()
api_key_entsoe = os.getenv("api_key_entsoe")

# Loading the Volue API key
client_id = os.getenv("volue_client_id")
client_secret = os.getenv("volue_client_secret")

solcast_api_key = os.getenv("solcast_api_key")
# ========================================================================Volue API setup================================================================
# Replace 'client_id' and 'client_secret' with your actual credentials
session = wapi.Session(client_id = client_id, client_secret = client_secret)

def fetch_token(client_id, client_secret):
    """
    Fetches a new access token using client credentials.

    Args:
        client_id (str): The client ID provided by Volue API.
        client_secret (str): The client secret provided by Volue API.

    Returns:
        dict: A dictionary containing the access token, token type, and expiration timestamp.
    """
    url = "https://auth.volueinsight.com/oauth2/token"
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials"}

    response = requests.post(url, headers=headers, data=data, auth=auth)
    
    if response.status_code == 200:
        token_info = response.json()
        # Calculate expiration timestamp
        expires_in = token_info.get("expires_in", 3600)  # Default to 3600 seconds
        expiration_timestamp = datetime.now() + timedelta(seconds=expires_in)
        
        return {
            "access_token": token_info["access_token"],
            "token_type": token_info["token_type"],
            "expires_at": expiration_timestamp
        }
    else:
        raise Exception("Failed to fetch token: " + response.text)

# Example usage:
# token_info = fetch_token(client_id, client_secret)
# print(token_info)

def is_token_valid(token_info):
    """
    Checks if the current access token is valid or needs to be refreshed.

    Args:
        token_info (dict): The dictionary containing the token information.

    Returns:
        bool: True if the token is valid, False otherwise.
    """
    if token_info is None:
        return False
    
    # Consider token as expired if it's close to expiration time (e.g., 5 minutes buffer)
    print(token_info["expires_at"], datetime.now() + timedelta(minutes=5))
    return datetime.now() + timedelta(minutes=5) < token_info["expires_at"]

# Usage example
# if not is_token_valid(token_info):
#   token_info = fetch_token(client_id, client_secret)
#   # Update your storage with the new token_info


# Fetching the curves==================================
# Wind curve horizon = days

def fetch_curve(token, curve_name):
    """
    Fetches curve data by name using the provided access token.

    Args:
        token (str): The access token for authorization.
        curve_name (str): The name of the curve to fetch.

    Returns:
        dict: A dictionary containing the curve data, or an error message.
    """
    url = "https://api.volueinsight.com/api/instances/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {
        "name": curve_name  # Filter by curve name
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code == 200:
        return response.json()  # Returns the curve data
    else:
        return {"error": "Failed to fetch curve data", "status_code": response.status_code, "message": response.text}

# Usage example (Assuming `token_info` is a dictionary containing your valid access token)
# curve_name = "pro ro wnd ec00 mwh/h cet min15 f"
# curve_data = fetch_curve(token_info['access_token'], curve_name)
# print(curve_data)


def fetch_time_series_data(token, curve_id, start_date, end_date, time_zone=None, output_time_zone=None, filter=None, function=None, frequency=None):
    """
    Fetches time series data for a specified curve.

    Args:
        token (str): The access token for authorization.
        curve_id (int): The ID of the curve to fetch data for.
        start_date (str): The start date for the data range in YYYY-MM-DD format.
        end_date (str): The end date for the data range in YYYY-MM-DD format.
        time_zone (str): Optional. The curve time zone before filtering and frequency change.
        output_time_zone (str): Optional. The curve time zone after filtering and frequency change.
        filter (str): Optional. Filter out parts of the time series.
        function (str): Optional. The aggregation/split function to use when changing frequency.
        frequency (str): Optional. The required frequency of the output.

    Returns:
        dict: A dictionary containing the time series data for the curve, or an error message.
    """
    url = f"https://api.volueinsight.com/api/series/{curve_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {
        "from": start_date,
        "to": end_date,
    }
    
    # Optional parameters
    if time_zone:
        params['time_zone'] = time_zone
    if output_time_zone:
        params['output_time_zone'] = output_time_zone
    if filter:
        params['filter'] = filter
    if function:
        params['function'] = function
    if frequency:
        params['frequency'] = frequency

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        return response.json()  # Returns the time series data for the curve
    else:
        return {"error": "Failed to fetch time series data", "status_code": response.status_code, "message": response.text}

# Function to get issue date - we'll use it internally for date fetching
def get_issue_date():
    issue_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return issue_date

#=========================================================================Imbalance State=========================================================================
# Function to fetch imbalance volumes for the intraday scenario
def fetching_imbalance_volumes():
    # Setting up the start and end dates (today and tomorrow)
    today = get_issue_date()
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    # Define the endpoint and parameters for fetching imbalance volumes via ENTSO-E API
    url = "https://web-api.tp.entsoe.eu/api"

    # Parameters for the API request
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A86",  # Document type for total imbalance volumes
        "controlArea_Domain": "10YRO-TEL------P",  # Romania's area EIC code
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,  # End date in yyyymmddhhmm format
    }

    # Headers for the API request
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Save the response content to a file
        with open("./entsoe_response.zip", "wb") as f:
            f.write(response.content)

        print("File saved as entsoe_response.zip")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")

# Define namespaces (try without namespaces if it doesn't match)
namespaces = {'ns': 'urn:iec62325.351:tc57wg16:451-6:balancingdocument:3:0'}

# Fetch and process imbalance volumes data from the existing zip file
def process_imbalance_volumes(zip_filepath='entsoe_response.zip'):
    # Extract the .zip file to access the XML content
    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
        extracted_files = zip_ref.namelist()
        xml_filename = extracted_files[0]  # Assuming there's only one file in the .zip
        zip_ref.extract(xml_filename)

    # Parse the XML content
    tree = ET.parse(xml_filename)
    root = tree.getroot()

    # Extract the namespace dynamically
    ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
    namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

    # Initialize lists to store parsed data
    timestamps_utc = []
    volumes = []

    # Iterate over all TimeSeries elements to differentiate by direction
    for timeseries in root.findall('ns:TimeSeries', namespaces):
        # Extract the direction information
        flow_direction_tag = timeseries.find('ns:flowDirection.direction', namespaces)
        
        if flow_direction_tag is not None:
            flow_direction = flow_direction_tag.text
        else:
            continue  # Skip this timeseries if no flow direction is found

        # Determine if the series is a deficit or an excedent based on flow direction
        if flow_direction == 'A02':  # A02 corresponds to deficit
            direction_sign = -1
        elif flow_direction == 'A01':  # A01 corresponds to excedent
            direction_sign = 1
        else:
            continue  # Skip if flow direction does not match either

        # Iterate over all Period elements within each TimeSeries
        for period in timeseries.findall('ns:Period', namespaces):
            start = period.find('ns:timeInterval/ns:start', namespaces)
            
            if start is None:
                continue

            start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

            # Iterate over all Point elements within each Period
            for point in period.findall('ns:Point', namespaces):
                position_tag = point.find('ns:position', namespaces)
                quantity_tag = point.find('ns:quantity', namespaces)

                # Handle missing tags with checks
                if position_tag is None or quantity_tag is None:
                    continue

                try:
                    position = int(position_tag.text)
                    quantity = float(quantity_tag.text) * direction_sign  # Apply direction sign here
                except ValueError as e:
                    print(f"Error converting position or quantity: {e}, skipping.")
                    continue

                # Calculate timestamp for the point based on position in UTC
                point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                
                # Append data to appropriate lists based on direction
                timestamps_utc.append(point_time_utc)
                volumes.append(quantity)

    # Create DataFrame from the parsed data
    df_imbalance = pd.DataFrame({
        'Timestamp_UTC': timestamps_utc,
        'Imbalance Volume': volumes
    })

    # Check if DataFrame is empty before further processing
    if df_imbalance.empty:
        print("DataFrame is empty after parsing XML. No data to process.")
        return df_imbalance

    # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
    df_imbalance['Timestamp_UTC'] = pd.to_datetime(df_imbalance['Timestamp_UTC'])
    df_imbalance['Timestamp_CET'] = df_imbalance['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

    # Remove the UTC column and rename CET to Timestamp for simplicity
    df_imbalance.drop(columns=['Timestamp_UTC'], inplace=True)
    df_imbalance.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Sort DataFrame by Timestamp to ensure it's ordered
    df_imbalance.sort_values(by='Timestamp', inplace=True)

    # Set up start and end dates dynamically for today (in CET)
    today = get_issue_date()
    start_of_day = pd.Timestamp(today.strftime('%Y-%m-%dT00:00:00'), tz='Europe/Berlin')
    end_of_day = pd.Timestamp(today.strftime('%Y-%m-%dT23:45:00'), tz='Europe/Berlin')

    # Filter Data to Keep Only the Relevant Day
    df_imbalance = df_imbalance[(df_imbalance['Timestamp'] >= start_of_day) & (df_imbalance['Timestamp'] <= end_of_day)]

    # Ensure all timestamps for the day are covered (assuming 15-min intervals)
    full_index_cet = pd.date_range(start=start_of_day, end=end_of_day + timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_imbalance = df_imbalance.set_index('Timestamp').reindex(full_index_cet, fill_value=0).rename_axis('Timestamp').reset_index()

    # **Shift All Timestamps One Interval Ahead**
    df_imbalance['Timestamp'] = df_imbalance['Timestamp'] + timedelta(minutes=15)

    # Adjusting the DataFrame to Include the Last Interval (`23:45 - 00:00`)
    final_end_time = pd.Timestamp(today.strftime('%Y-%m-%dT23:45:00'), tz='Europe/Berlin') + timedelta(minutes=15)
    df_imbalance = df_imbalance[(df_imbalance['Timestamp'] >= start_of_day + timedelta(minutes=15)) & (df_imbalance['Timestamp'] <= final_end_time)]

    # Clean up: remove the extracted files after processing
    os.remove(xml_filename)

    # Return the final DataFrame
    return df_imbalance

def fetch_imbalance_prices():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Define the endpoint and parameters for fetching imbalance prices via ENTSO-E API
    url = "https://web-api.tp.entsoe.eu/api"

    # Parameters for the API request
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A85",  # Document type for imbalance prices
        "controlArea_Domain": "10YRO-TEL------P",  # Romania's area EIC code
        "periodStart": start_cet.strftime('%Y%m%d%H%M'),  # Start date in yyyymmddhhmm format
        "periodEnd": end_cet.strftime('%Y%m%d%H%M'),  # End date in yyyymmddhhmm format
    }

    # Headers for the API request
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Save the response content to a file
        with open("./imbalance_prices_response.zip", "wb") as f:
            f.write(response.content)

        print("File saved as imbalance_prices_response.zip")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")

def process_imbalance_prices(zip_filepath='imbalance_prices_response.zip'):
    # Extract the .zip file to access the XML content
    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
        extracted_files = zip_ref.namelist()
        xml_filename = extracted_files[0]  # Assuming there's only one file in the .zip
        zip_ref.extract(xml_filename)

    # Parse the XML content
    tree = ET.parse(xml_filename)
    root = tree.getroot()

    # Extract the namespace dynamically if needed
    ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
    namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}
    print(f"Using namespace: {namespaces}")

    # Dictionary to store data by timestamp
    data_dict = {}

    # Iterate over all TimeSeries elements
    for timeseries_index, timeseries in enumerate(root.findall('ns:TimeSeries', namespaces)):
        # Extract Business Type
        business_type_tag = timeseries.find('ns:businessType', namespaces)

        if business_type_tag is None:
            print(f"[TimeSeries {timeseries_index}] No business type found, skipping...")
            continue

        business_type = business_type_tag.text
        print(f"[TimeSeries {timeseries_index}] Found Business Type: {business_type}")

        # Filter based on business type (only interested in imbalance prices A19)
        if business_type != "A19":
            print(f"[TimeSeries {timeseries_index}] Unknown Business Type: {business_type}, skipping...")
            continue

        # Iterate over all Period elements within each TimeSeries
        for period_index, period in enumerate(timeseries.findall('ns:Period', namespaces)):
            start = period.find('ns:timeInterval/ns:start', namespaces)

            if start is None:
                print(f"[TimeSeries {timeseries_index} - Period {period_index}] Period start time not found, skipping...")
                continue

            start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC
            print(f"[TimeSeries {timeseries_index} - Period {period_index}] Period start time (UTC): {start_time_utc}")

            # Iterate over all Point elements within each Period
            for point_index, point in enumerate(period.findall('ns:Point', namespaces)):
                position_tag = point.find('ns:position', namespaces)
                price_tag = point.find('ns:imbalance_Price.amount', namespaces)
                category_tag = point.find('ns:imbalance_Price.category', namespaces)

                # Handle missing tags with checks
                if position_tag is None or price_tag is None or category_tag is None:
                    print(f"[TimeSeries {timeseries_index} - Period {period_index} - Point {point_index}] Missing position, price, or category tag: {ET.tostring(point, encoding='unicode')}")
                    continue

                try:
                    position = int(position_tag.text)
                    price = float(price_tag.text)
                    category = category_tag.text
                    print(f"[TimeSeries {timeseries_index} - Period {period_index} - Point {point_index}] Extracted Point - Position: {position}, Price: {price}, Category: {category}")
                except ValueError as e:
                    print(f"Error converting position or price: {e}, skipping.")
                    continue

                # Calculate timestamp for the point based on position in UTC
                point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))

                # Store data in dictionary by timestamp
                if point_time_utc not in data_dict:
                    data_dict[point_time_utc] = {'Excedent Price': None, 'Deficit Price': None}

                if category == 'A04':  # Excedent Price
                    data_dict[point_time_utc]['Excedent Price'] = price
                elif category == 'A05':  # Deficit Price
                    data_dict[point_time_utc]['Deficit Price'] = price
                else:
                    print(f"Unknown category: {category}, skipping...")

    # Convert dictionary to DataFrame
    df_prices = pd.DataFrame.from_dict(data_dict, orient='index').reset_index()
    df_prices.rename(columns={'index': 'Timestamp_UTC'}, inplace=True)

    # Drop duplicate timestamps to avoid issues with reindexing
    df_prices['Timestamp_UTC'] = pd.to_datetime(df_prices['Timestamp_UTC'])
    df_prices = df_prices.drop_duplicates(subset='Timestamp_UTC', keep='first')

    # Check if DataFrame is empty before further processing
    if df_prices.empty:
        print("DataFrame is empty after parsing XML. No data to process.")
        return df_prices

    # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
    df_prices['Timestamp_CET'] = df_prices['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

    # Remove the UTC column and rename CET to Timestamp for simplicity
    df_prices.drop(columns=['Timestamp_UTC'], inplace=True)
    df_prices.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Sort DataFrame by Timestamp to ensure it's ordered
    df_prices.sort_values(by='Timestamp', inplace=True)

    # Filter Data to Keep Only the Relevant Day (adjust the date range accordingly)
    start_of_day = pd.Timestamp('2024-12-04T00:00:00', tz='Europe/Berlin')
    end_of_day = pd.Timestamp('2024-12-04T23:45:00', tz='Europe/Berlin')
    df_prices = df_prices[(df_prices['Timestamp'] >= start_of_day) & (df_prices['Timestamp'] <= end_of_day)]

    # Ensure all timestamps for the day are covered (assuming 15-min intervals)
    full_index_cet = pd.date_range(start=start_of_day, end=end_of_day + timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_prices = df_prices.set_index('Timestamp').reindex(full_index_cet).rename_axis('Timestamp').reset_index()

    # Clean up: remove the extracted files after processing
    os.remove(xml_filename)

    # Return the final DataFrame
    return df_prices

def create_combined_imbalance_dataframe(df_prices, df_volumes):
    """
    This function combines the imbalance prices and volumes into a single DataFrame.
    
    Parameters:
    df_prices (DataFrame): DataFrame containing timestamps, Excedent Price, and Deficit Price.
    df_volumes (DataFrame): DataFrame containing timestamps and Imbalance Volume.

    Returns:
    DataFrame: Combined DataFrame with Timestamp, Excedent Price, Deficit Price, and Imbalance Volume.
    """
    # Merge prices and volumes on the Timestamp column using an outer join to ensure all data is included.
    df_combined = pd.merge(df_prices, df_volumes, on='Timestamp', how='outer')

    # Sort the combined DataFrame by Timestamp to keep it in chronological order.
    df_combined = df_combined.sort_values(by='Timestamp')

    # Fill any missing values with 0.0 to ensure consistency in analysis.
    df_combined.fillna(0.0, inplace=True)

    return df_combined

#==========================================================================Wind Production==============================================================================
def fetch_process_wind_notified():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A69",  # Document type for wind and solar generation forecast
        "processType": "A18",   # A01 represents Day Ahead forecast
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B19"  # B19 corresponds to Wind Onshore
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)

                if start is None:
                    continue

                # Extract the start time and assume a 15-min resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_notified_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Notified Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_notified_production['Timestamp_UTC'] = pd.to_datetime(df_notified_production['Timestamp_UTC'])
        df_notified_production['Timestamp_CET'] = df_notified_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_notified_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_notified_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_notified_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_notified_production = df_notified_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: Start from 00:15 for intraday alignment
        df_notified_production['Timestamp'] = df_notified_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_notified_production['Notified Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_notified_production_excel = df_notified_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_notified_production_excel['Timestamp'] = df_notified_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_notified_production_excel.to_excel("./data_fetching/Entsoe/Notified_Production_Wind.xlsx", index=False)

        # Return the final DataFrame
        return df_notified_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_wind_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B19"  # B19 corresponds to Wind Onshore
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_actual_production_excel = df_actual_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_production_excel['Timestamp'] = df_actual_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_production_excel.to_excel("./data_fetching/Entsoe/Actual_Production_Wind.xlsx", index=False)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def combine_wind_production_data(df_notified, df_actual, df_forecast):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_notified, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Notified Production (MW)', 'Actual Production (MW)']

    # Step 5: Add the volue forecast to the combined dataframe by aligning timestamps
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')

    # Aligning the forecast data based on the timestamps in the combined dataframe
    df_combined = df_combined.set_index('Timestamp')
    df_combined['Volue Forecast (MW)'] = df_forecast['Volue Forecast (MW)']
    df_combined.reset_index(inplace=True)

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

def fetch_volue_wind_data():
    # INSTANCES curve 15 min
    today = get_issue_date()
    curve = session.get_curve(name='pro ro wnd ec00 mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    df_wind_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_wind_15min = df_wind_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_wind_15min

def fetch_volue_wind_data_15min():
    # INSTANCES curve 15 min
    today = get_issue_date()
    curve = session.get_curve(name='pro ro wnd intraday lastec mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    df_wind_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_wind_15min = df_wind_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_wind_15min
    
# Adjusting the Volue forecast DataFrame
def preprocess_volue_forecast(df_forecast):
    # Assuming the forecast dataframe is using the index as the timestamp
    # Reset the index to make it a proper column
    df_forecast.reset_index(inplace=True)

    # Rename the index column to 'Timestamp' for consistency with other dataframes
    df_forecast.rename(columns={'index': 'Timestamp'}, inplace=True)
    # Step 5: Rename the forecast column to match our desired name
    df_forecast.rename(columns={df_forecast.columns[-1]: 'Volue Forecast (MW)'}, inplace=True)

    # Sort by timestamp to align with the other dataframes
    df_forecast = df_forecast.sort_values(by='Timestamp')

    # Shift all timestamps forward by 1 hour
    df_forecast['Timestamp'] = pd.to_datetime(df_forecast['Timestamp'])

    return df_forecast

# Solcast Forecast======================================
def fetching_Cogealac_data_15min():
    lat = 44.561156
    lon = 28.562586
    # Fetch data from the API
    api_url = "https://api.solcast.com.au/data/forecast/radiation_and_weather?latitude={}&longitude={}&hours=168&output_parameters=air_temp,wind_direction_100m,wind_direction_10m,wind_speed_100m,wind_speed_10m&period=PT15M&format=csv&api_key={}".format(lat, lon, solcast_api_key)
    response = requests.get(api_url)
    print("Fetching data...")
    if response.status_code == 200:
        # Write the content to a CSV file
        with open("./data_fetching/Solcast/Wind_dataset_raw_15min.csv", 'wb') as file:
            file.write(response.content)
    else:
        print(response.text)  # Add this line to see the error message returned by the API
        raise Exception(f"Failed to fetch data: Status code {response.status_code}")

def predicting_wind_production_15min():
    # Loading the input dataset
    data = pd.read_csv("./data_fetching/Solcast/Wind_dataset_raw_15min.csv")
    forecast_dataset = pd.read_excel("./Wind_Production_Forecast/Input_Wind_dataset_15min.xlsx")

    data['period_end'] = pd.to_datetime(data['period_end'], errors='coerce', format='%Y-%m-%dT%H:%M:%SZ')
    # Shift the 'period_end' column by 2 hours
    data['period_end'] = data['period_end'] + pd.Timedelta(hours=1)
    forecast_dataset['Data'] = data.period_end.dt.strftime('%Y-%m-%d').values
    # Creating the Interval column
    forecast_dataset['Interval'] = data.period_end.dt.hour * 4 + data.period_end.dt.minute // 15 + 1
    # Replace NaNs in the 'Interval' column with 0
    forecast_dataset['Interval'].fillna(9, inplace=True)
    # Replace NaNs in the 'Date' column with the previous valid observation
    forecast_dataset['Data'].fillna(method='ffill', inplace=True)
    # Completing the wind_direction_100m column
    forecast_dataset["wind_direction_100m"] = data["wind_direction_100m"].values
    # Completing the wind_direction_10m column
    forecast_dataset["wind_direction_10m"] = data["wind_direction_10m"].values
    # Completing the wind_speed_100m column
    forecast_dataset["wind_speed_100m"] = data["wind_speed_100m"].values
    # Completing the wind_speed_10m column
    forecast_dataset["wind_speed_10m"] = data["wind_speed_10m"].values
    # Completing the temperature column
    forecast_dataset["temperature"] = data["air_temp"].values

    xgb_loaded = joblib.load("./Wind_Production_Forecast/rs_xgb_wind_production_quarterly_1024.pkl")

    forecast_dataset["Month"] = pd.to_datetime(forecast_dataset.Data).dt.month
    dataset = forecast_dataset.copy()
    forecast_dataset = forecast_dataset.drop("Data", axis=1)
    forecast_dataset = forecast_dataset[["Interval", "wind_direction_100m", "wind_direction_10m", "wind_speed_100m", "wind_speed_10m", "temperature", "Month"]]
    preds = xgb_loaded.predict(forecast_dataset.values)

    # Rounding each value in the list to the third decimal
    rounded_values = [round(value, 3) for value in preds]

    #Exporting Results to Excel
    workbook = xlsxwriter.Workbook("./Wind_Production_Forecast/Wind_Forecast_Production_15min.xlsx")
    worksheet = workbook.add_worksheet("Production_Predictions")
    date_format = workbook.add_format({'num_format':'dd.mm.yyyy'})
    # Define a format for cells with three decimal places
    decimal_format = workbook.add_format({'num_format': '0.000'})
    row = 1
    col = 0
    worksheet.write(0,0,"Data")
    worksheet.write(0,1,"Interval")
    worksheet.write(0,2,"Prediction")

    for value in rounded_values:
        worksheet.write(row, col + 2, value, decimal_format)
        row += 1
    row = 1
    for Data, Interval in zip(dataset.Data, dataset.Interval):
        worksheet.write(row, col + 0, Data, date_format)
        worksheet.write(row, col + 1, Interval)
        row += 1

    workbook.close()

    # Create Timestamp column
    dataset['Timestamp'] = dataset.apply(
        lambda row: pd.Timestamp(f"{row['Data']}") + pd.Timedelta(minutes=15 * (row['Interval'] - 1)),
        axis=1
    )
    dataset['Timestamp'] = dataset['Timestamp'].dt.tz_localize('CET')

    # Add predictions to the dataset
    dataset['Prediction (MW)'] = rounded_values

    # Select the relevant columns to return
    final_forecast_df = dataset[['Timestamp', 'Prediction (MW)']]

    return final_forecast_df

def add_solcast_forecast_to_wind_dataframe(df_combined, df_solcast_forecast):
    # Ensure both dataframes are sorted and indexed properly by 'Timestamp'
    df_combined = df_combined.sort_values(by='Timestamp').set_index('Timestamp')

    # Convert the Timestamp in solcast forecast to datetime and set appropriate timezone
    df_solcast_forecast['Timestamp'] = pd.to_datetime(df_solcast_forecast['Timestamp'], utc=True).dt.tz_convert('Europe/Berlin')

    # Set the Timestamp as the index for reindexing purposes
    df_solcast_forecast = df_solcast_forecast.set_index('Timestamp')

    # Remove any duplicates in solcast forecast index to avoid reindexing issues
    df_solcast_forecast = df_solcast_forecast[~df_solcast_forecast.index.duplicated(keep='first')]

    # Check and correct the column name for forecast values
    print("Columns in Solcast Forecast DataFrame:", df_solcast_forecast.columns)

    # Replace 'Solcast_Forecast' with the correct column name if necessary
    forecast_column = 'Prediction (MW)'  # Replace with correct name if different
    if forecast_column not in df_solcast_forecast.columns:
        raise KeyError(f"Column '{forecast_column}' not found in df_solcast_forecast. Available columns: {df_solcast_forecast.columns}")

    # Align the Solcast dataframe with the full index of the combined wind dataframe
    full_index = df_combined.index.union(df_solcast_forecast.index).sort_values()

    # Remove duplicate labels in the full index as well to ensure uniqueness
    full_index = full_index.drop_duplicates()

    # Reindex the solcast forecast DataFrame
    df_solcast_forecast = df_solcast_forecast.reindex(full_index).fillna(method='ffill')

    # Remove any existing name from the index to prevent duplication during reset_index
    df_solcast_forecast.index.name = None

    # Reset index to make Timestamp a column again
    df_solcast_forecast.reset_index(inplace=True)

    # Keep only the relevant timestamps that match with the combined dataframe
    df_solcast_forecast = df_solcast_forecast[df_solcast_forecast['index'].isin(df_combined.index)]

    # Rename 'index' to 'Timestamp' for clarity
    df_solcast_forecast.rename(columns={'index': 'Timestamp'}, inplace=True)

    # Add the Solcast forecast to the combined wind dataframe
    df_combined = df_combined.reset_index()
    df_combined['Solcast Forecast (MW)'] = df_solcast_forecast.set_index('Timestamp')[forecast_column].reindex(df_combined['Timestamp']).values

    return df_combined

#==========================================================================Solar Production==============================================================================
def fetch_process_solar_notified():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A69",  # Document type for wind and solar generation forecast
        "processType": "A18",   # A01 represents Day Ahead forecast
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B16"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)

                if start is None:
                    continue

                # Extract the start time and assume a 15-min resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_notified_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Notified Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_notified_production['Timestamp_UTC'] = pd.to_datetime(df_notified_production['Timestamp_UTC'])
        df_notified_production['Timestamp_CET'] = df_notified_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_notified_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_notified_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_notified_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_notified_production = df_notified_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: Start from 00:15 for intraday alignment
        df_notified_production['Timestamp'] = df_notified_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_notified_production['Notified Production (MW)'].fillna(0, inplace=True)

         # Writing the Notified Production to Excel
        df_notified_production_excel = df_notified_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_notified_production_excel['Timestamp'] = df_notified_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_notified_production_excel.to_excel("./data_fetching/Entsoe/Notified_Production_Solar.xlsx", index=False)

        # Return the final DataFrame
        return df_notified_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_solar_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B16"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_actual_production_excel = df_actual_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_production_excel['Timestamp'] = df_actual_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_production_excel.to_excel("./data_fetching/Entsoe/Actual_Production_Solar.xlsx", index=False)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_volue_solar_data():
    today = get_issue_date()
    # INSTANCE curve 15 min
    curve = session.get_curve(name='pro ro spv ec00 mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    pd_s_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_solar_15min = pd_s_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_solar_15min

def combine_solar_production_data(df_notified, df_actual, df_forecast):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_notified, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Notified Production (MW)', 'Actual Production (MW)']

    # Step 5: Add the volue forecast to the combined dataframe by aligning timestamps
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')

    # Aligning the forecast data based on the timestamps in the combined dataframe
    df_combined = df_combined.set_index('Timestamp')
    df_combined['Volue Forecast (MW)'] = df_forecast['Volue Forecast (MW)']
    df_combined.reset_index(inplace=True)

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

#==========================================================================Hydro Production==============================================================================

def fetch_process_hydro_water_reservoir_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B12"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_hydro_river_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B11"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_volue_hydro_data():
    today = get_issue_date()
    # INSTANCE curve hour
    curve = session.get_curve(name='pro ro hydro tot mwh/h cet h f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_h = curve.get_instance(issue_date=today)
    pd_s_h = ts_h.to_pandas() # convert TS object to pandas.Series object
    pd_df_h = pd_s_h.to_frame() # convert pandas.Series to pandas.DataFrame

    return pd_df_h

def align_and_combine_hydro_data(df_notified, df_actual, df_volue_forecast):
    # Ensure notified and actual dataframes are sorted and set the index to Timestamp
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')

    # Load volue forecast data and rename the columns appropriately
    df_volue_forecast = df_volue_forecast.rename(columns={df_volue_forecast.columns[0]: 'Volue Forecast (MW)'})
    
    # Convert Volue forecast index to datetime and set the timezone to CET (Europe/Berlin)
    df_volue_forecast.index = pd.to_datetime(df_volue_forecast.index, utc=True).tz_convert('Europe/Berlin')
    
    # Resample the volue forecast to 15-minute intervals using linear interpolation
    df_volue_forecast_resampled = df_volue_forecast.resample('15T').interpolate(method='linear')
    
    # Reset the index of the resampled forecast and rename the index to Timestamp
    df_volue_forecast_resampled = df_volue_forecast_resampled.reset_index().rename(columns={'index': 'Timestamp'})

    # Align the resampled volue forecast DataFrame to the combined notified and actual hydro production data
    df_combined = pd.concat([df_notified, df_actual], axis=1).reset_index()
    df_combined['Volue Forecast (MW)'] = df_volue_forecast_resampled.set_index('Timestamp')['Volue Forecast (MW)'].reindex(df_combined['Timestamp']).values

    # Sort the final DataFrame by Timestamp and fill any NaN values with zeros
    df_combined.sort_values(by='Timestamp', inplace=True)
    df_combined.fillna(0, inplace=True)

    # Rename columns for clarity
    df_combined.columns = ['Timestamp', 'Hydro Reservoir Actual (MW)', 'Hydro River Actual (MW)', 'Volue Forecast (MW)']
    df_combined["Hydro Actual (MW)"] = df_combined["Hydro Reservoir Actual (MW)"] + df_combined["Hydro River Actual (MW)"]
    # Writing the Hydro Production to Excel
    df_combined_excel = df_combined.copy()
    # Remove timezone information from the 'Timestamp' column
    df_combined_excel['Timestamp'] = df_combined_excel['Timestamp'].dt.tz_localize(None)
    # Save to Excel
    df_combined_excel.to_excel("./data_fetching/Entsoe/Hydro_Production.xlsx", index=False)
    return df_combined

# Fetch and process both datasets
# df_wind_notified = fetch_process_wind_notified()
# df_wind_actual = fetch_process_wind_actual_production()
# df_wind_volue_forecast = preprocess_volue_forecast(fetch_volue_wind_data())

# df_wind_production = combine_wind_production_data(df_wind_notified, df_wind_actual, df_wind_volue_forecast)
# st.dataframe(df_wind_production)

# fetching_Cogealac_data_15min()
# df_wind_solcast_forecast = predicting_wind_production_15min()
# st.dataframe(df_wind_solcast_forecast)

# df_wind_final = add_solcast_forecast_to_wind_dataframe(df_wind_production, df_wind_solcast_forecast)
# st.dataframe(df_wind_final)

# df_solar_notified = fetch_process_solar_notified()
# df_solar_actual = fetch_process_solar_actual_production()
# df_solar = combine_solar_production_data(df_solar_notified, df_solar_actual)
# st.dataframe(df_solar)

# df_hydro_reservoir_actual = fetch_process_hydro_water_reservoir_actual_production()
# df_hydro_river_actual = fetch_process_hydro_river_actual_production()
# df_hydro_volue = fetch_volue_hydro_data()
# df_hydro = align_and_combine_hydro_data(df_hydro_reservoir_actual, df_hydro_river_actual, df_hydro_volue)

# st.dataframe(df_hydro)

#==========================================================================Consumption==============================================================================
def fetch_consumption_forecast():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A65",
        "processType": "A01",  # For notified consumption
        "outBiddingZone_Domain": "10YRO-TEL------P",
        "periodStart": period_start,  # Start period as per Postman
        "periodEnd": period_end    # End period as per Postman
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
       
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        quantities = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame
        df_forecast = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Forecasted Consumption (MW)': quantities
        })

        df_forecast['Timestamp_UTC'] = pd.to_datetime(df_forecast['Timestamp_UTC'])
        df_forecast['Timestamp_CET'] = df_forecast['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_forecast.drop(columns=['Timestamp_UTC'], inplace=True)
        df_forecast.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Slice to intraday values
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        df_forecast = df_forecast[(df_forecast['Timestamp'] >= start_of_day) & (df_forecast['Timestamp'] <= end_of_day)]

        # Writing the Consumption Forecast to Excel
        df_forecast_excel = df_forecast.copy()
        # Remove timezone information from the 'Timestamp' column
        df_forecast_excel['Timestamp'] = df_forecast_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_forecast_excel.to_excel("./data_fetching/Entsoe/Consumption_Forecast.xlsx", index=False)

        return df_forecast

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_actual_consumption():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A65",  # Document type for actual load
        "processType": "A16",   # A16 represents Realized consumption
        "outBiddingZone_Domain": "10YRO-TEL------P",  # EIC code for Romania
        "periodStart": period_start,
        "periodEnd": period_end,
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        quantities = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame
        df_actual = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Consumption (MW)': quantities
        })

        df_actual['Timestamp_UTC'] = pd.to_datetime(df_actual['Timestamp_UTC'])
        df_actual['Timestamp_CET'] = df_actual['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_actual.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Writing the Consumption Actual to Excel
        df_actual_excel = df_actual.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_excel['Timestamp'] = df_actual_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_excel.to_excel("./data_fetching/Entsoe/Consumption_Actual.xlsx", index=False)

        return df_actual

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def combine_consumption_data(df_forecast, df_actual):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_forecast, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Consumption Forecast (MW)', 'Actual Consumption (MW)']

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    # df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

#==========================================================================CrossBorder Flaws==================================================================

#1.a) RO_BG Physical Flows========================================================================================================

def fetch_physical_flows_bulgaria_to_romania():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": "10YCA-BULGARIA-R",  # Bulgaria's EIC code
        "in_Domain": "10YRO-TEL------P",  # Romania's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def fetch_physical_flows_romania_to_bulgaria():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": "10YRO-TEL------P",  # Romania's EIC code
        "in_Domain": "10YCA-BULGARIA-R",  # Bulgaria's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def concatenate_cross_border_flows(df_bg_to_ro, df_ro_to_bg):
    # Rename columns for clarity before concatenation
    df_bg_to_ro = df_bg_to_ro.rename(columns={"Physical Flow (MW)": "BG → RO Flow (MW)"})
    df_ro_to_bg = df_ro_to_bg.rename(columns={"Physical Flow (MW)": "RO → BG Flow (MW)"})

    # Merge dataframes on 'Timestamp'
    df_cross_border_flows = pd.merge(df_bg_to_ro, df_ro_to_bg, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure correct chronological order
    df_cross_border_flows = df_cross_border_flows.sort_values(by='Timestamp').reset_index(drop=True)

    return df_cross_border_flows

#1.b) RO_BG Crossborder Schedule===================================================================================================

eic_Bulgaria = "10YCA-BULGARIA-R"

def process_entsoe_response(response, start_cet, end_cet):
    """ Process ENTSO-E XML response and return a DataFrame with scheduled flows. """
    
    root = ET.fromstring(response.content)  # Ensure response is correctly passed
    namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

    timestamps_utc = []
    flows = []

    for timeseries in root.findall('ns:TimeSeries', namespaces):
        for period in timeseries.findall('ns:Period', namespaces):
            start_time = period.find('ns:timeInterval/ns:start', namespaces)
            resolution = period.find('ns:resolution', namespaces)

            if start_time is None or resolution is None:
                continue

            start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

            position_data = {}  # Store extracted position data

            for point in period.findall('ns:Point', namespaces):
                position_tag = point.find('ns:position', namespaces)
                quantity_tag = point.find('ns:quantity', namespaces)

                if position_tag is None or quantity_tag is None:
                    continue

                try:
                    position = int(position_tag.text)
                    flow = float(quantity_tag.text)
                    position_data[position] = flow  # Store extracted flow data
                except ValueError as e:
                    print(f"Error converting position or quantity: {e}, skipping.")
                    continue

            # Ensure we have a full 24-hour schedule by filling missing positions
            for position in range(1, 25):  # 1 to 24 positions (1-based index)
                if position not in position_data:
                    if position > 1:
                        # Forward-fill from previous hour
                        position_data[position] = position_data.get(position - 1, 0)
                    else:
                        # Default to 0 for first position if no previous value
                        position_data[position] = 0

            # Generate timestamps and match extracted data
            for position, flow in position_data.items():
                point_time_utc = start_time_utc + timedelta(hours=(position - 1))
                timestamps_utc.append(point_time_utc)
                flows.append(flow)

    df_schedule = pd.DataFrame({'Timestamp_UTC': timestamps_utc, 'Scheduled Flow (MW)': flows})

    if df_schedule.empty:
        print("No data found, returning a dataframe with 0 values.")
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        return pd.DataFrame({'Timestamp': full_index, 'Scheduled Flow (MW)': 0})

    df_schedule['Timestamp_UTC'] = pd.to_datetime(df_schedule['Timestamp_UTC'])
    df_schedule['Timestamp_CET'] = df_schedule['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
    df_schedule.drop(columns=['Timestamp_UTC'], inplace=True)
    df_schedule.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Generate full quarter-hourly timestamps for CET timezone
    full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_schedule = df_schedule.set_index('Timestamp').reindex(full_index).reset_index()
    df_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

    df_schedule['Scheduled Flow (MW)'] = df_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(0)

    return df_schedule

def fetch_cross_border_schedule(out_domain, in_domain):
    """
    Fetches the cross-border scheduled flow from ENTSO-E API, processes it, and returns a cleaned DataFrame.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    # Convert to UTC for API request
    start_utc = start_cet.tz_convert('UTC') - timedelta(hours=2)
    period_start = start_utc.strftime('%Y%m%d%H%M')
    period_end = end_cet.tz_convert('UTC').strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A09",
        "out_Domain": out_domain,
        "in_Domain": in_domain,
        "periodStart": period_start,
        "periodEnd": period_end,
        "contract_MarketAgreement.Type": "A05"
    }
    headers = {"Content-Type": "application/xml", "Accept": "application/xml"}

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return process_entsoe_response(response, start_cet, end_cet)
    
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        return create_zeroed_crossborder_schedule(start_cet, end_cet)

# Function to create a DataFrame filled with zeros if no data is available
def create_zeroed_crossborder_schedule(start_cet, end_cet):
    timestamps = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    return pd.DataFrame({'Timestamp': timestamps, 'Scheduled Flow (MW)': [0] * len(timestamps)})

# Function to convert hourly data to quarter-hourly intervals
def expand_to_quarterly_intervals(df_schedule, start_cet, end_cet):
    expanded_data = []
    for _, row in df_schedule.iterrows():
        timestamp = row['Timestamp']
        value = row['Scheduled Flow (MW)']
        for i in range(4):  # 4 intervals per hour
            expanded_data.append({'Timestamp': timestamp + pd.Timedelta(minutes=15 * i), 'Scheduled Flow (MW)': value})

    df_quarterly_schedule = pd.DataFrame(expanded_data)
    full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_quarterly_schedule = df_quarterly_schedule.set_index('Timestamp').reindex(full_index).reset_index()
    df_quarterly_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)
    df_quarterly_schedule['Scheduled Flow (MW)'] = df_quarterly_schedule['Scheduled Flow (MW)'].fillna(0)

    return df_quarterly_schedule

def fetch_cross_border_schedule_with_fallback(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    # Fetch starting from the last UTC hour of the previous day
    start_utc = start_cet.tz_convert('UTC') - timedelta(hours=1)
    period_start = start_utc.strftime('%Y%m%d%H%M')
    period_end = end_cet.tz_convert('UTC').strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": out_domain,  # Outgoing country EIC code
        "in_Domain": in_domain,   # Incoming country EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end,  # End period
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(hours=(position - 1))
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Handle cases with no data or single point
        if not timestamps_utc:
            print("No hourly data in the response. Creating fallback DataFrame with zeros.")
            hourly_index = pd.date_range(start=start_cet, end=end_cet - timedelta(hours=1), freq='H', tz='Europe/Berlin')
            df_hourly = pd.DataFrame({'Timestamp': hourly_index, 'Scheduled Flow (MW)': 0})
        else:
            # Create hourly DataFrame
            df_schedule = pd.DataFrame({
                'Timestamp_UTC': timestamps_utc,
                'Scheduled Flow (MW)': flows
            })

            df_schedule['Timestamp_UTC'] = pd.to_datetime(df_schedule['Timestamp_UTC'])
            df_schedule['Timestamp_CET'] = df_schedule['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
            df_schedule.drop(columns=['Timestamp_UTC'], inplace=True)
            df_schedule.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

            # Filter for the current day in CET (00:00 to 23:45)
            df_schedule = df_schedule[(df_schedule['Timestamp'] >= start_cet) & (df_schedule['Timestamp'] < end_cet)]

            # Extrapolate hourly data to quarterly intervals
            expanded_data = []
            for index, row in df_schedule.iterrows():
                timestamp = row['Timestamp']
                value = row['Scheduled Flow (MW)']

                for i in range(4):
                    expanded_data.append({
                        'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                        'Scheduled Flow (MW)': value
                    })

            df_quarterly_schedule = pd.DataFrame(expanded_data)

            # Ensure complete index for intraday
            full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
            df_quarterly_schedule = df_quarterly_schedule.set_index('Timestamp').reindex(full_index).reset_index()
            df_quarterly_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

            # Forward-fill and backward-fill missing values
            df_quarterly_schedule['Scheduled Flow (MW)'] = df_quarterly_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(method='bfill')

            return df_quarterly_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        # Create fallback DataFrame with zeros
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        return pd.DataFrame({'Timestamp': full_index, 'Scheduled Flow (MW)': 0})

#1.c) Combining the Flows and Schedules into a dataframe=============================================================================
def combine_physical_and_scheduled_flows_ro_bg(df_cross_border_flows, df_bg_to_ro_schedule, df_ro_to_bg_schedule):
    # Rename schedule columns for clarity
    df_bg_to_ro_schedule = df_bg_to_ro_schedule.rename(columns={"Scheduled Flow (MW)": "BG → RO Scheduled Flow (MW)"})
    df_ro_to_bg_schedule = df_ro_to_bg_schedule.rename(columns={"Scheduled Flow (MW)": "RO → BG Scheduled Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_cross_border_flows, df_bg_to_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_to_bg_schedule, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

#2. RO_RS Flows========================================================================================================

def fetch_physical_flows(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": out_domain,  # Other's EIC code
        "in_Domain": in_domain,  # Inner's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def combine_physical_and_scheduled_flows_ro_rs(df_rs_ro_flow, df_ro_rs_flow, df_rs_ro_schedule, df_ro_rs_schedule):
    # Rename schedule columns for clarity
    df_ro_rs_schedule = df_ro_rs_schedule.rename(columns={"Scheduled Flow (MW)": "RO → RS Scheduled Flow (MW)"})
    df_rs_ro_schedule = df_rs_ro_schedule.rename(columns={"Scheduled Flow (MW)": "RS → RO Scheduled Flow (MW)"})
    df_ro_rs_flow = df_ro_rs_flow.rename(columns={"Physical Flow (MW)": "RO → RS Physical Flow (MW)"})
    df_rs_ro_flow = df_rs_ro_flow.rename(columns={"Physical Flow (MW)": "RS → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_rs_schedule, df_rs_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_rs_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_rs_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Serbia = "10YCS-SERBIATSOV"
eic_Romania = "10YRO-TEL------P"

#3. RO_UA Flaws========================================================================================================

def combine_physical_and_scheduled_flows_ro_ua(df_ua_ro_flow, df_ro_ua_flow, df_ua_ro_schedule, df_ro_ua_schedule):
    # Rename schedule columns for clarity
    df_ro_ua_schedule = df_ro_ua_schedule.rename(columns={"Scheduled Flow (MW)": "RO → UA Scheduled Flow (MW)"})
    df_ua_ro_schedule = df_ua_ro_schedule.rename(columns={"Scheduled Flow (MW)": "UA → RO Scheduled Flow (MW)"})
    df_ro_ua_flow = df_ro_ua_flow.rename(columns={"Physical Flow (MW)": "RO → UA Physical Flow (MW)"})
    df_ua_ro_flow = df_ua_ro_flow.rename(columns={"Physical Flow (MW)": "UA → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_ua_schedule, df_ua_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_ua_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ua_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Ukraine = "10Y1001C--000182"

#4. RO_MD Flows========================================================================================================

def combine_physical_and_scheduled_flows_ro_md(df_md_ro_flow, df_ro_md_flow, df_md_ro_schedule, df_ro_md_schedule):
    # Rename schedule columns for clarity
    df_ro_md_schedule = df_ro_md_schedule.rename(columns={"Scheduled Flow (MW)": "RO → MD Scheduled Flow (MW)"})
    df_md_ro_schedule = df_md_ro_schedule.rename(columns={"Scheduled Flow (MW)": "MD → RO Scheduled Flow (MW)"})
    df_ro_md_flow = df_ro_md_flow.rename(columns={"Physical Flow (MW)": "RO → MD Physical Flow (MW)"})
    df_md_ro_flow = df_md_ro_flow.rename(columns={"Physical Flow (MW)": "MD → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_md_schedule, df_md_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_md_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_md_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Moldova = "10Y1001A1001A990"

#5. RO_HU Flows========================================================================================================

def fetch_physical_flows_ro_hu(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin') - timedelta(hours=2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": out_domain,  # Hungary's EIC code
        "in_Domain": in_domain,  # Romania's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create DataFrame
        df_physical_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_physical_flows['Timestamp_UTC'] = pd.to_datetime(df_physical_flows['Timestamp_UTC'])
        df_physical_flows['Timestamp_CET'] = df_physical_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_physical_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_physical_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        return df_physical_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def fetch_cross_border_schedule_hu_ro():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = (start_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # Start in UTC for API
    period_end = (end_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # End in UTC for API

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": "10YHU-MAVIR----U",  # Outgoing country EIC code
        "in_Domain": "10YRO-TEL------P",   # Incoming country EIC code
        "periodStart": period_start,  # Start period in UTC
        "periodEnd": period_end,  # End period in UTC
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        # Extract the time interval and resolution
        start_time_utc = root.find('.//ns:TimeSeries/ns:Period/ns:timeInterval/ns:start', namespaces)
        resolution = root.find('.//ns:TimeSeries/ns:Period/ns:resolution', namespaces)

        if start_time_utc is None or resolution is None or resolution.text != "PT15M":
            print("No valid timeInterval or resolution detected.")
            return pd.DataFrame()

        start_time_utc = datetime.strptime(start_time_utc.text, '%Y-%m-%dT%H:%MZ')

        # Extract points
        positions = []
        quantities = []
        for point in root.findall('.//ns:Point', namespaces):
            position_tag = point.find('ns:position', namespaces)
            quantity_tag = point.find('ns:quantity', namespaces)

            if position_tag is not None and quantity_tag is not None:
                try:
                    positions.append(int(position_tag.text))
                    quantities.append(float(quantity_tag.text))
                except ValueError:
                    continue

        # Generate a full range of timestamps
        full_positions = list(range(1, 97))
        full_quantities = []

        # Fill missing positions by interpolating
        for i in full_positions:
            if i in positions:
                full_quantities.append(quantities[positions.index(i)])
            else:
                # Use the last known value if missing
                full_quantities.append(full_quantities[-1] if full_quantities else 0)

        # Generate timestamps in UTC
        timestamps_utc = [
            start_time_utc + timedelta(minutes=15 * (i - 1)) for i in full_positions
        ]

        # Convert timestamps to CET
        timestamps_cet = [
            pd.Timestamp(ts).tz_localize('UTC').tz_convert('Europe/Berlin')
            for ts in timestamps_utc
        ]

        # Create DataFrame
        df_schedule = pd.DataFrame({
            'Timestamp': timestamps_cet,
            'Scheduled Flow (MW)': full_quantities
        })

        # Filter only the current day's intervals in CET
        df_schedule = df_schedule[
            (df_schedule['Timestamp'] >= start_cet) &
            (df_schedule['Timestamp'] < end_cet)
        ].reset_index(drop=True)

        return df_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Scheduled Flow (MW)"])  # Return empty DataFrame on failure

def fetch_cross_border_schedule_quarterly_ro_hu():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = (start_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # Start in UTC for API
    period_end = (end_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # End in UTC for API

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": "10YRO-TEL------P",  # Outgoing country EIC code
        "in_Domain": "10YHU-MAVIR----U",   # Incoming country EIC code
        "periodStart": period_start,  # Start period in UTC
        "periodEnd": period_end,  # End period in UTC
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        # Extract timeInterval
        start_time_utc = root.find('.//ns:TimeSeries/ns:Period/ns:timeInterval/ns:start', namespaces)
        resolution = root.find('.//ns:TimeSeries/ns:Period/ns:resolution', namespaces)

        if start_time_utc is None or resolution is None or resolution.text != "PT15M":
            print("No valid timeInterval or resolution detected.")
            return pd.DataFrame()

        start_time_utc = datetime.strptime(start_time_utc.text, '%Y-%m-%dT%H:%MZ')

        # Extract points
        positions = []
        quantities = []
        for point in root.findall('.//ns:Point', namespaces):
            position_tag = point.find('ns:position', namespaces)
            quantity_tag = point.find('ns:quantity', namespaces)

            if position_tag is None or quantity_tag is None:
                continue

            try:
                positions.append(int(position_tag.text))
                quantities.append(float(quantity_tag.text))
            except ValueError as e:
                print(f"Error converting position or quantity: {e}, skipping.")
                continue

        # Generate timestamps
        timestamps_utc = [
            start_time_utc + timedelta(minutes=15 * (pos - 1))
            for pos in positions
        ]

        # Convert to CET
        timestamps_cet = [
            pd.Timestamp(ts).tz_localize('UTC').tz_convert('Europe/Berlin')
            for ts in timestamps_utc
        ]

        # Create DataFrame
        df_schedule = pd.DataFrame({
            'Timestamp': timestamps_cet,
            'Scheduled Flow (MW)': quantities
        })

        # Filter only the desired CET range
        df_schedule = df_schedule[
            (df_schedule['Timestamp'] >= start_cet) &
            (df_schedule['Timestamp'] < end_cet)
        ]

        # Ensure the dataframe includes exactly 96 intervals
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        df_schedule = df_schedule.set_index('Timestamp').reindex(full_index).reset_index()
        df_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

        # Forward-fill and backward-fill as needed
        df_schedule['Scheduled Flow (MW)'] = df_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(method='bfill')

        return df_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Scheduled Flow (MW)"])  # Return empty DataFrame on failure


def combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled):
    # Rename schedule columns for clarity
    df_ro_hu_scheduled = df_ro_hu_scheduled.rename(columns={"Scheduled Flow (MW)": "RO → HU Scheduled Flow (MW)"})
    df_hu_ro_scheduled = df_hu_ro_scheduled.rename(columns={"Scheduled Flow (MW)": "HU → RO Scheduled Flow (MW)"})
    df_ro_hu_flow = df_ro_hu_flow.rename(columns={"Physical Flow (MW)": "RO → HU Physical Flow (MW)"})
    df_hu_ro_flow = df_hu_ro_flow.rename(columns={"Physical Flow (MW)": "HU → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_hu_scheduled, df_hu_ro_scheduled, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_hu_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_hu_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Hungary = "10YHU-MAVIR----U" 

# IGCC Flows===============================================================================================================
def fetch_igcc_netting_flows():
	"""Fetch real-time activated balancing energy data from Transelectrica API for today in CET.
	Note: The API endpoint used seems to be for general activated balancing energy,
	not specifically IGCC netting. The field names 'imbalanceNettingImport' and
	'imbalanceNettingExport' are used, but might return 0 if not present in the response.
	"""

	# Define CET timezone
	cet_timezone = pytz.timezone("Europe/Berlin")

	# Get current time in CET and set midnight as start of the day
	cet_now = datetime.now(cet_timezone)
	cet_midnight = cet_now.replace(hour=0, minute=0, second=0, microsecond=0)

	# Convert CET midnight to UTC (Transelectrica API operates in UTC)
	utc_midnight = cet_midnight.astimezone(pytz.utc)
	utc_now = cet_now.astimezone(pytz.utc)

	# Set API time range: Fetch from CET midnight (converted to UTC) until now
	from_time = utc_midnight.strftime("%Y-%m-%dT%H:%M:%S.000Z")
	to_time = utc_now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

	# API Request (Transelectrica)
	# This URL fetches activatedBalancingEnergyOverview
	url = f"https://newmarkets.transelectrica.ro/usy-durom-publicreportg01/00121002500000000000000000000100/publicReport/estimatedPowerSystemImbalance?timeInterval.from={from_time}&timeInterval.to={to_time}&pageInfo.pageSize=3000"

	try:
		response = requests.get(url)
		# Check response status
		if response.status_code != 200:
			print(f"❌ Failed to fetch data for IGCC flows. Status code: {response.status_code}")
			return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])

		# Parse JSON response
		data = response.json()
		items = data.get("itemList", [])

		print(f"✅ Successfully fetched {len(items)} records for IGCC processing from API.")

		# Process and convert timestamps
		rows = []
		for item in items:
			try:
				# Convert timestamps from UTC to CET
				utc_from = datetime.fromisoformat(item['timeInterval']['from'].replace('Z', '+00:00'))
				cet_from = utc_from.astimezone(cet_timezone)

				# Use 'imbalanceNettingImport' and 'imbalanceNettingExport' fields if they exist
				netting_import = float(item.get("imbalanceNettingImport", 0) or 0)
				netting_export = float(item.get("imbalanceNettingExport", 0) or 0)

				rows.append([cet_from, netting_import, netting_export])
			except Exception as item_error:
				print(f"Error processing item for IGCC data: {item_error}") # Catch errors for individual items

		if not rows:
			print("⚠️ No valid IGCC data rows processed.")
			return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])

		df = pd.DataFrame(rows, columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
		# Ensure Timestamp column is datetime and remove timezone for consistency if needed later
		df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
		# Writing the Consumption Forecast to Excel
		df_igcc = df.copy()
		# Remove timezone information from the 'Timestamp' column
		df_igcc['Timestamp'] = df_igcc['Timestamp'].dt.tz_localize(None)
		# Save to Excel
		df_igcc.to_excel("./data_fetching/Entsoe/IGCC_Netting_Flows.xlsx", index=False)

		return df

	except requests.exceptions.RequestException as req_error:
		print(f"❌ Request failed for IGCC data: {req_error}")
		return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
	except Exception as json_error: # Catch JSON parsing errors or other issues
		print(f"❌ Error processing IGCC data response: {json_error}")
		return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
    
#===================================================================Rendering and Testing=================================================================================

def render_test_entsoe_newapi_functions():
    # df_hu_ro_flow = fetch_physical_flows_ro_hu(eic_Hungary, eic_Romania)
    # df_ro_hu_flow = fetch_physical_flows_ro_hu(eic_Romania, eic_Hungary)

    # df_hu_ro_scheduled = fetch_cross_border_schedule_hu_ro()
    # df_ro_hu_scheduled = fetch_cross_border_schedule_quarterly_ro_hu()

    # df_ro_hu = combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled)
    # st.dataframe(df_ro_hu)

    #6. Storing the deviations in a dataframe========================================================================================================

    # Creating the deviations dataframe
    # Sample setup for timestamps (replace with actual range from your data)
    # timestamps = pd.date_range(start='2024-12-12 00:00:00', periods=96, freq='15T')

    # # Placeholder data for deviations from each source (replace these with actual calculations)
    # wind_deviation_actual = df_wind['Actual Production (MW)'] - df_wind['Notified Production (MW)']
    # wind_deviation_forecast_value = df_wind['Volue Forecast (MW)'] - df_wind['Notified Production (MW)']
    # wind_deviation_forecast_solcast = df_wind['Solcast Forecast (MW)'] - df_wind['Notified Production (MW)']

    # # Placeholder for other variables - replace with actual calculations
    # solar_deviation = [0] * len(timestamps)  # Example placeholder
    # hydro_deviation = [0] * len(timestamps)
    # consumption_deviation = [0] * len(timestamps)

    # # Cross-border deviations placeholders (replace with scheduled - actual flows)
    # hu_ro_deviation = [0] * len(timestamps)
    # ro_hu_deviation = [0] * len(timestamps)

    # # Construct the combined deviations DataFrame
    # df_deviations = pd.DataFrame({
    #     'Timestamp': df_wind['Timestamp'],
    #     'Wind_Deviation_Actual': wind_deviation_actual,
    #     'Wind_Deviation_Forecast_Value': wind_deviation_forecast_value,
    #     'Wind_Deviation_Forecast_Solcast': wind_deviation_forecast_solcast,
    #     'Solar_Deviation': solar_deviation,
    #     'Hydro_Deviation': hydro_deviation,
    #     'Consumption_Deviation': consumption_deviation,
    #     'HU_RO_Deviation': hu_ro_deviation,
    #     'RO_HU_Deviation': ro_hu_deviation
    # })

    # # Replace NaN or unavailable deviations with 0 or forecasts
    # df_deviations.fillna(0, inplace=True)

    # # Display the resulting combined deviations DataFrame
    # st.dataframe(df_deviations)

    # Wind Analysis================================================================================
    # df_wind_notified = fetch_process_wind_notified()
    # df_wind_actual = fetch_process_wind_actual_production()
    # st.dataframe(df_wind_actual)
    # df_wind_volue = preprocess_volue_forecast(fetch_volue_wind_data())
    # df_wind = combine_wind_production_data(df_wind_notified, df_wind_actual, df_wind_volue)

    # fetching_Cogealac_data_15min()
    # df_wind_solcast = predicting_wind_production_15min()
    # df_wind = add_solcast_forecast_to_wind_dataframe(df_wind, df_wind_solcast)
    # st.dataframe(df_wind)


    # # Replace 0 with NaN in 'Actual Production (MW)' to identify missing values
    # df_wind['Actual Production (MW)'] = df_wind['Actual Production (MW)'].replace(0, None)

    # # Add columns to indicate when forecasts should be used
    # df_wind['Volue Forecast (Filtered)'] = df_wind['Volue Forecast (MW)'].where(df_wind['Actual Production (MW)'].isna())
    # df_wind['Solcast Forecast (Filtered)'] = df_wind['Solcast Forecast (MW)'].where(df_wind['Actual Production (MW)'].isna())

    # df_wind = df_wind[df_wind["Notified Production (MW)"] > 0] 

    # # Create a long-format DataFrame for Plotly
    # df_wind_long = df_wind.melt(
    #     id_vars=['Timestamp'],
    #     value_vars=[
    #         'Actual Production (MW)',
    #         'Notified Production (MW)',
    #         'Volue Forecast (Filtered)',
    #         'Solcast Forecast (Filtered)'
    #     ],
    #     var_name='Type',
    #     value_name='Production (MW)'
    # )

    # # Remove rows where 'Production (MW)' is NaN
    # df_wind_long = df_wind_long[df_wind_long['Production (MW)'].notna()]


    # # Interactive dashboard header
    # st.header("Wind Production Monitoring")

    # # Plotting Actual vs Notified Wind Production with Forecasts
    # st.write("### Actual vs Notified Wind Production Over Time (With Forecasts)")
    # fig_wind_forecast = px.line(
    #     df_wind_long,
    #     x='Timestamp',
    #     y='Production (MW)',
    #     color='Type',
    #     line_dash='Type',
    #     labels={'Production (MW)': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual vs Notified Wind Production (With Forecasts)"
    # )

    # # Customize styles: Notified should always be solid
    # fig_wind_forecast.for_each_trace(lambda trace: trace.update(line_dash=None) if trace.name == 'Notified Production (MW)' else None)

    # # Show the plot
    # st.plotly_chart(fig_wind_forecast, use_container_width=True)

    # # Assuming df_wind is your DataFrame with the following columns:
    # # 'Timestamp', 'Notified Production (MW)', 'Actual Production (MW)', 'Volue Forecast (MW)', 'Solcast Forecast (MW)'

    # # Step 1: Compute deviations
    # df_wind['Deviation_Actual'] = df_wind['Actual Production (MW)'] - df_wind['Notified Production (MW)']
    # df_wind['Deviation_ValueForecast'] = df_wind['Volue Forecast (MW)'] - df_wind['Notified Production (MW)']
    # df_wind['Deviation_SolcastForecast'] = df_wind['Solcast Forecast (MW)'] - df_wind['Notified Production (MW)']

    # # Step 2: Combine Actual and Forecasted Deviations
    # df_wind['Deviation_Final'] = np.where(
    #     df_wind['Actual Production (MW)'].notna(),
    #     df_wind['Deviation_Actual'],
    #     np.nan
    # )

    # # For forecasting periods
    # forecast_mask = df_wind['Actual Production (MW)'].isna()
    # df_wind.loc[forecast_mask, 'Deviation_Final_ValueForecast'] = df_wind['Deviation_ValueForecast']
    # df_wind.loc[forecast_mask, 'Deviation_Final_SolcastForecast'] = df_wind['Deviation_SolcastForecast']

    # # Remove the last timestamp where 'Notified Production (MW)' is incomplete (e.g., NaN or 0)
    # df_wind_filtered = df_wind[df_wind['Notified Production (MW)'] > 0]

    # # Plotting the deviations
    # fig = px.line(df_wind_filtered, x='Timestamp', y='Deviation_Final',
    #               labels={'Deviation_Final': 'Deviation (MW)', 'Timestamp': 'Timestamp'},
    #               title="Actual and Forecasted Deviations from Notified Production")

    # # Add Value Forecast as dashed line
    # fig.add_scatter(x=df_wind_filtered['Timestamp'], 
    #                 y=df_wind_filtered['Deviation_Final_ValueForecast'], 
    #                 mode='lines', 
    #                 line=dict(dash='dash', color='red'), 
    #                 name='Deviation - Value Forecast')

    # # Add Solcast Forecast as dashed line
    # fig.add_scatter(x=df_wind_filtered['Timestamp'], 
    #                 y=df_wind_filtered['Deviation_Final_SolcastForecast'], 
    #                 mode='lines', 
    #                 line=dict(dash='dash', color='orange'), 
    #                 name='Deviation - Solcast Forecast')

    # fig.update_layout(
    #     xaxis_title='Timestamp',
    #     yaxis_title='Deviation (MW)',
    #     legend_title='Type'
    # )

    # # Display the chart in Streamlit
    # st.plotly_chart(fig, use_container_width=True)



    # Solar Production Analysis===================================================================
    # df_solar_notified = fetch_process_solar_notified()
    # df_solar_actual = fetch_process_solar_actual_production()
    # df_solar_volue = preprocess_volue_forecast(fetch_volue_solar_data())
    # df_solar = combine_solar_production_data(df_solar_notified, df_solar_actual, df_solar_volue)

    # st.dataframe(df_solar)

    # import pandas as pd
    # import plotly.express as px
    # import streamlit as st
    # import numpy as np


    # # Step 1: Identify the last interval with actual production > 0
    # last_actual_index = df_solar[df_solar['Actual Production (MW)'] > 0].index.max()

    # # Step 2: Compute Deviations
    # df_solar['Deviation_Actual'] = df_solar['Actual Production (MW)'] - df_solar['Notified Production (MW)']
    # df_solar['Deviation_Forecast'] = df_solar['Volue Forecast (MW)'] - df_solar['Notified Production (MW)']

    # # Step 3: Split the data for solid and dashed lines
    # df_solar['Deviation_Combined'] = np.where(
    #     df_solar.index <= last_actual_index,
    #     df_solar['Deviation_Actual'],
    #     np.nan
    # )
    # df_solar['Deviation_Forecast_Line'] = np.where(
    #     df_solar.index > last_actual_index,
    #     df_solar['Deviation_Forecast'],
    #     np.nan
    # )

    # # Step 4: Visualization for Actual vs Notified Solar Production
    # st.title("Solar Production Monitoring")

    # # Plot 1: Actual vs Notified Solar Production Over Time
    # st.subheader("Actual vs Notified Solar Production Over Time")
    # fig_actual_vs_notified = px.line(
    #     df_solar, 
    #     x='Timestamp', 
    #     y=['Notified Production (MW)', 'Actual Production (MW)', 'Volue Forecast (MW)'],
    #     labels={'value': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual vs Notified Solar Production (With Forecast)"
    # )
    # # Update line styles
    # fig_actual_vs_notified.update_traces(selector=dict(name='Notified Production (MW)'),
    #                                      line=dict(color='blue', dash='solid'))
    # fig_actual_vs_notified.update_traces(selector=dict(name='Actual Production (MW)'),
    #                                      line=dict(color='skyblue', dash='solid'))
    # fig_actual_vs_notified.update_traces(selector=dict(name='Volue Forecast (MW)'),
    #                                      line=dict(color='orange', dash='dash'))

    # st.plotly_chart(fig_actual_vs_notified, use_container_width=True)

    # # Plot 2: Actual and Forecasted Deviations
    # st.subheader("Actual and Forecasted Deviations from Notified Solar Production")
    # fig_deviation = px.line(
    #     df_solar, 
    #     x='Timestamp', 
    #     y=['Deviation_Combined', 'Deviation_Forecast_Line'],
    #     labels={'value': 'Deviation (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual and Forecasted Deviations from Notified Solar Production"
    # )
    # # Update line styles
    # fig_deviation.update_traces(selector=dict(name='Deviation_Combined'),
    #                             line=dict(color='skyblue', dash='solid'),
    #                             name="Deviation - Actual Production")
    # fig_deviation.update_traces(selector=dict(name='Deviation_Forecast_Line'),
    #                             line=dict(color='orange', dash='dash'),
    #                             name="Deviation - Forecast")

    # st.plotly_chart(fig_deviation, use_container_width=True)


    # Step 6: Add Deviations to Centralized DataFrame
    # df_deviations['Solar_Deviation'] = df_solar['Solar_Deviation_Final']

    # Hydro Production Analysis=========================================================================================

    # df_hydro_reservoir_actual = fetch_process_hydro_water_reservoir_actual_production()
    # df_hydro_river_actual = fetch_process_hydro_river_actual_production()
    # df_hydro_volue = fetch_volue_hydro_data()
    # df_hydro = align_and_combine_hydro_data(df_hydro_reservoir_actual, df_hydro_river_actual, df_hydro_volue)

    # st.dataframe(df_hydro)

    # # Combine Actual Hydro Production (Hydro Reservoir + Hydro River)
    # df_hydro['Hydro_Actual'] = df_hydro['Hydro Reservoir Actual (MW)'] + df_hydro['Hydro River Actual (MW)']

    # # Identify the last interval with actual production > 0
    # last_actual_index = df_hydro[df_hydro['Hydro_Actual'] > 0].index.max()

    # # Replace Hydro Actual after the last valid production with None
    # df_hydro.loc[last_actual_index + 1:, 'Hydro_Actual'] = None

    # # Melt the DataFrame to long format for Plotly Express
    # df_hydro_long = df_hydro.melt(id_vars=['Timestamp'], 
    #                               value_vars=['Hydro_Actual', 'Volue Forecast (MW)'],
    #                               var_name='Type', value_name='Production')

    # # Create line dash mapping
    # line_dash_map = {
    #     'Hydro_Actual': 'solid',
    #     'Volue Forecast (MW)': 'dash'
    # }

    # # Visualization: Hydro Actual vs Forecast
    # st.header("Hydro Production Monitoring")
    # st.subheader("Actual vs Forecasted Hydro Production")

    # fig_hydro_actual_forecast = px.line(
    #     df_hydro_long, 
    #     x='Timestamp', 
    #     y='Production', 
    #     color='Type', 
    #     line_dash='Type',
    #     line_dash_map=line_dash_map,
    #     title="Actual vs Forecasted Hydro Production",
    #     labels={'Production': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     color_discrete_map={
    #         'Hydro_Actual': 'blue',
    #         'Volue Forecast (MW)': 'orange'
    #     }
    # )

    # st.plotly_chart(fig_hydro_actual_forecast, use_container_width=True)

    # Consumption Analysis=========================================================================================

    # df_consumption_forecast = fetch_consumption_forecast()
    # df_consumption_actual = fetch_actual_consumption()
    # df_consumption = combine_consumption_data(df_consumption_forecast, df_consumption_actual)
    # st.dataframe(df_consumption)

    # import plotly.express as px
    # import streamlit as st

    # # Load the existing DataFrame
    # # Replace this with the actual dataframe you already have

    # # Drop rows with None in 'Actual Consumption (MW)' for clean plotting
    # df_actual = df_consumption.dropna(subset=['Actual Consumption (MW)'])

    # # Plotly Graph: Actual vs Forecasted Consumption
    # fig = px.line()

    # # Add Actual Consumption line
    # fig.add_scatter(
    #     x=df_actual['Timestamp'],
    #     y=df_actual['Actual Consumption (MW)'],
    #     mode='lines',
    #     name='Actual Consumption (MW)',
    #     line=dict(color='#1f77b4')  # Blue line
    # )

    # # Add Forecasted Consumption line
    # fig.add_scatter(
    #     x=df_consumption['Timestamp'],
    #     y=df_consumption['Consumption Forecast (MW)'],
    #     mode='lines',
    #     name='Consumption Forecast (MW)',
    #     line=dict(color='#ff7f0e')  # Orange line
    # )

    # # Update layout for clarity
    # fig.update_layout(
    #     title="Actual vs Forecasted Consumption Over Time",
    #     xaxis_title="Timestamp",
    #     yaxis_title="Consumption (MW)",
    #     legend_title="Type",
    #     template="plotly_dark",
    #     hovermode="x unified"
    # )

    # # Display in Streamlit
    # st.header("Consumption Monitoring")
    # st.write("### Actual vs Forecasted Consumption Over Time")
    # st.plotly_chart(fig, use_container_width=True)

    # Cross Border Analysis=========================================================================================

    # RO_BG
    df_physical_flow_bg_ro = fetch_physical_flows_bulgaria_to_romania()
    df_physical_flow_ro_bg = fetch_physical_flows_romania_to_bulgaria()
    df_physical_flows_ro_bg = concatenate_cross_border_flows(df_physical_flow_bg_ro, df_physical_flow_ro_bg)
    df_scheduled_flow_ro_bg = fetch_cross_border_schedule(eic_Romania, eic_Bulgaria)
    df_scheduled_flow_bg_ro = fetch_cross_border_schedule(eic_Bulgaria, eic_Romania)
    df_ro_bg = combine_physical_and_scheduled_flows_ro_bg(df_physical_flows_ro_bg, df_scheduled_flow_bg_ro, df_scheduled_flow_ro_bg)


    def calculate_excedent_deficit_ro_bg(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → BG Scheduled Flow (MW)'] - df['BG → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → BG Flow (MW)'] - df['BG → RO Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    # Apply the logic to your dataframe
    df_ro_bg = calculate_excedent_deficit_ro_bg(df_ro_bg, "Excedent_RO_BG (MW)", "Deficit_RO_BG (MW)")


    st.dataframe(df_ro_bg)

    # RO_RS
    def calculate_excedent_deficit_ro_rs(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → RS Scheduled Flow (MW)'] - df['RS → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → RS Physical Flow (MW)'] - df['RS → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df
    df_physical_flow_ro_rs = fetch_physical_flows(eic_Romania, eic_Serbia)
    df_physical_flow_rs_ro = fetch_physical_flows(eic_Serbia, eic_Romania)
    df_crossborder_flow_ro_rs = fetch_cross_border_schedule(eic_Romania, eic_Serbia)
    df_crossborder_flow_rs_ro = fetch_cross_border_schedule(eic_Serbia, eic_Romania)
    df_ro_rs = combine_physical_and_scheduled_flows_ro_rs(df_physical_flow_rs_ro, df_physical_flow_ro_rs, df_crossborder_flow_rs_ro, df_crossborder_flow_ro_rs)
    # Apply the logic to your dataframe
    df_ro_rs = calculate_excedent_deficit_ro_rs(df_ro_rs, "Excedent_RO_RS (MW)", "Deficit_RO_RS (MW)")
    st.dataframe(df_ro_rs)

    # RO_HU
    def calculate_excedent_deficit_ro_hu(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → HU Scheduled Flow (MW)'] - df['HU → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → HU Physical Flow (MW)'] - df['HU → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_hu_ro_flow = fetch_physical_flows_ro_hu(eic_Hungary, eic_Romania)
    df_ro_hu_flow = fetch_physical_flows_ro_hu(eic_Romania, eic_Hungary)

    df_hu_ro_scheduled = fetch_cross_border_schedule_hu_ro()
    df_ro_hu_scheduled = fetch_cross_border_schedule_quarterly_ro_hu()

    df_ro_hu = combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled)
    # Apply the logic to your dataframe
    df_ro_hu = calculate_excedent_deficit_ro_hu(df_ro_hu, "Excedent_RO_HU (MW)", "Deficit_RO_HU (MW)")
    st.dataframe(df_ro_hu)

    # RO_MD
    def calculate_excedent_deficit_ro_md(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → MD Scheduled Flow (MW)'] - df['MD → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → MD Physical Flow (MW)'] - df['MD → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_physical_flow_ro_md = fetch_physical_flows(eic_Romania, eic_Moldova)
    df_physical_flow_md_ro = fetch_physical_flows(eic_Moldova, eic_Romania)
    df_crossborder_flow_ro_md = fetch_cross_border_schedule(eic_Romania, eic_Moldova)
    df_crossborder_flow_md_ro = fetch_cross_border_schedule(eic_Moldova, eic_Romania)

    df_ro_md = combine_physical_and_scheduled_flows_ro_md(df_physical_flow_md_ro, df_physical_flow_ro_md, df_crossborder_flow_md_ro, df_crossborder_flow_ro_md)
    # # Apply the logic to your dataframe
    df_ro_md = calculate_excedent_deficit_ro_md(df_ro_md, "Excedent_RO_MD (MW)", "Deficit_RO_MD (MW)")
    st.dataframe(df_ro_md)

    # RO_UA
    def calculate_excedent_deficit_ro_ua(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → UA Scheduled Flow (MW)'] - df['UA → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → UA Physical Flow (MW)'] - df['UA → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_physical_flow_ro_ua = fetch_physical_flows(eic_Romania, eic_Ukraine)
    df_physical_flow_ua_ro = fetch_physical_flows(eic_Ukraine, eic_Romania)
    df_crossborder_flow_ro_ua = fetch_cross_border_schedule(eic_Romania, eic_Ukraine)
    df_crossborder_flow_ua_ro = fetch_cross_border_schedule(eic_Ukraine, eic_Romania)
    df_ro_ua = combine_physical_and_scheduled_flows_ro_ua(df_physical_flow_ua_ro, df_physical_flow_ro_ua, df_crossborder_flow_ua_ro, df_crossborder_flow_ro_ua)

    # Apply the logic to your dataframe
    df_ro_ua = calculate_excedent_deficit_ro_ua(df_ro_ua, "Excedent_RO_UA (MW)", "Deficit_RO_UA (MW)")
    st.dataframe(df_ro_ua)

    # Creating the Deficit and Excedent dataframe to store all deficits and excedents
    def create_aligned_deficit_excedent_dataframe(df_ro_bg, df_ro_rs, df_ro_hu, df_ro_md, df_ro_ua):
        # Select the required columns and rename for clarity
        ro_bg = df_ro_bg[['Timestamp', 'Excedent_RO_BG (MW)', 'Deficit_RO_BG (MW)']].copy()
        ro_rs = df_ro_rs[['Timestamp', 'Excedent_RO_RS (MW)', 'Deficit_RO_RS (MW)']].copy()
        ro_hu = df_ro_hu[['Timestamp', 'Excedent_RO_HU (MW)', 'Deficit_RO_HU (MW)']].copy()
        ro_md = df_ro_md[['Timestamp', 'Excedent_RO_MD (MW)', 'Deficit_RO_MD (MW)']].copy()
        ro_ua = df_ro_ua[['Timestamp', 'Excedent_RO_UA (MW)', 'Deficit_RO_UA (MW)']].copy()

        # Merge all dataframes on Timestamp to align them
        aligned_df = pd.merge(ro_bg, ro_rs, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_hu, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_md, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_ua, on='Timestamp', how='outer')
        # Fill NaN values with 0, assuming missing values mean no deficit/excedent
        aligned_df.fillna(0, inplace=True)

        # Calculate Total Excedent and Total Deficit
        aligned_df['Total Excedent (MW)'] = (
            aligned_df['Excedent_RO_BG (MW)'] +
            aligned_df['Excedent_RO_RS (MW)'] +
            aligned_df['Excedent_RO_HU (MW)'] +
            aligned_df['Excedent_RO_MD (MW)'] +
            aligned_df['Excedent_RO_UA (MW)']
        )
        aligned_df['Total Deficit (MW)'] = (
            aligned_df['Deficit_RO_BG (MW)'] +
            aligned_df['Deficit_RO_RS (MW)'] +
            aligned_df['Deficit_RO_HU (MW)'] +
            aligned_df['Deficit_RO_MD (MW)'] +
            aligned_df['Deficit_RO_UA (MW)']
        )

        # Calculate the System Direction
        aligned_df['System Direction (MW)'] = (
        aligned_df['Total Excedent (MW)'] - aligned_df['Total Deficit (MW)']
        )
        
        # Sort by Timestamp for a cleaner view
        aligned_df.sort_values(by='Timestamp', inplace=True)

        # Writing the Border Flows to Excel
        aligned_df_excel = aligned_df.copy()
        # Remove timezone information from the 'Timestamp' column
        aligned_df_excel['Timestamp'] = aligned_df_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        aligned_df_excel.to_excel("./data_fetching/Entsoe/Border_Flows.xlsx", index=False)

        return aligned_df

    # Example usage:
    df_deficit_excedent_aligned = create_aligned_deficit_excedent_dataframe(df_ro_bg, df_ro_rs, df_ro_hu, df_ro_md, df_ro_ua)

    st.dataframe(df_deficit_excedent_aligned)
import streamlit as st
import zipfile
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta
import requests
import os
from dotenv import load_dotenv
import numpy as np
import wapi
import joblib
import xlsxwriter
import matplotlib.pyplot as plt
import pytz
# import plotly.express as px

# Load environment variables for ENTSO-E API key
load_dotenv()
api_key_entsoe = os.getenv("api_key_entsoe")

# Loading the Volue API key
client_id = os.getenv("volue_client_id")
client_secret = os.getenv("volue_client_secret")

solcast_api_key = os.getenv("solcast_api_key")
# ========================================================================Volue API setup================================================================
# Replace 'client_id' and 'client_secret' with your actual credentials
session = wapi.Session(client_id = client_id, client_secret = client_secret)

def fetch_token(client_id, client_secret):
    """
    Fetches a new access token using client credentials.

    Args:
        client_id (str): The client ID provided by Volue API.
        client_secret (str): The client secret provided by Volue API.

    Returns:
        dict: A dictionary containing the access token, token type, and expiration timestamp.
    """
    url = "https://auth.volueinsight.com/oauth2/token"
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials"}

    response = requests.post(url, headers=headers, data=data, auth=auth)
    
    if response.status_code == 200:
        token_info = response.json()
        # Calculate expiration timestamp
        expires_in = token_info.get("expires_in", 3600)  # Default to 3600 seconds
        expiration_timestamp = datetime.now() + timedelta(seconds=expires_in)
        
        return {
            "access_token": token_info["access_token"],
            "token_type": token_info["token_type"],
            "expires_at": expiration_timestamp
        }
    else:
        raise Exception("Failed to fetch token: " + response.text)

# Example usage:
# token_info = fetch_token(client_id, client_secret)
# print(token_info)

def is_token_valid(token_info):
    """
    Checks if the current access token is valid or needs to be refreshed.

    Args:
        token_info (dict): The dictionary containing the token information.

    Returns:
        bool: True if the token is valid, False otherwise.
    """
    if token_info is None:
        return False
    
    # Consider token as expired if it's close to expiration time (e.g., 5 minutes buffer)
    print(token_info["expires_at"], datetime.now() + timedelta(minutes=5))
    return datetime.now() + timedelta(minutes=5) < token_info["expires_at"]

# Usage example
# if not is_token_valid(token_info):
#   token_info = fetch_token(client_id, client_secret)
#   # Update your storage with the new token_info


# Fetching the curves==================================
# Wind curve horizon = days

def fetch_curve(token, curve_name):
    """
    Fetches curve data by name using the provided access token.

    Args:
        token (str): The access token for authorization.
        curve_name (str): The name of the curve to fetch.

    Returns:
        dict: A dictionary containing the curve data, or an error message.
    """
    url = "https://api.volueinsight.com/api/instances/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {
        "name": curve_name  # Filter by curve name
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code == 200:
        return response.json()  # Returns the curve data
    else:
        return {"error": "Failed to fetch curve data", "status_code": response.status_code, "message": response.text}

# Usage example (Assuming `token_info` is a dictionary containing your valid access token)
# curve_name = "pro ro wnd ec00 mwh/h cet min15 f"
# curve_data = fetch_curve(token_info['access_token'], curve_name)
# print(curve_data)


def fetch_time_series_data(token, curve_id, start_date, end_date, time_zone=None, output_time_zone=None, filter=None, function=None, frequency=None):
    """
    Fetches time series data for a specified curve.

    Args:
        token (str): The access token for authorization.
        curve_id (int): The ID of the curve to fetch data for.
        start_date (str): The start date for the data range in YYYY-MM-DD format.
        end_date (str): The end date for the data range in YYYY-MM-DD format.
        time_zone (str): Optional. The curve time zone before filtering and frequency change.
        output_time_zone (str): Optional. The curve time zone after filtering and frequency change.
        filter (str): Optional. Filter out parts of the time series.
        function (str): Optional. The aggregation/split function to use when changing frequency.
        frequency (str): Optional. The required frequency of the output.

    Returns:
        dict: A dictionary containing the time series data for the curve, or an error message.
    """
    url = f"https://api.volueinsight.com/api/series/{curve_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {
        "from": start_date,
        "to": end_date,
    }
    
    # Optional parameters
    if time_zone:
        params['time_zone'] = time_zone
    if output_time_zone:
        params['output_time_zone'] = output_time_zone
    if filter:
        params['filter'] = filter
    if function:
        params['function'] = function
    if frequency:
        params['frequency'] = frequency

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        return response.json()  # Returns the time series data for the curve
    else:
        return {"error": "Failed to fetch time series data", "status_code": response.status_code, "message": response.text}

# Function to get issue date - we'll use it internally for date fetching
def get_issue_date():
    issue_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return issue_date

#=========================================================================Imbalance State=========================================================================
# Function to fetch imbalance volumes for the intraday scenario
def fetching_imbalance_volumes():
    # Setting up the start and end dates (today and tomorrow)
    today = get_issue_date()
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    # Define the endpoint and parameters for fetching imbalance volumes via ENTSO-E API
    url = "https://web-api.tp.entsoe.eu/api"

    # Parameters for the API request
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A86",  # Document type for total imbalance volumes
        "controlArea_Domain": "10YRO-TEL------P",  # Romania's area EIC code
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,  # End date in yyyymmddhhmm format
    }

    # Headers for the API request
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Save the response content to a file
        with open("./entsoe_response.zip", "wb") as f:
            f.write(response.content)

        print("File saved as entsoe_response.zip")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")

# Define namespaces (try without namespaces if it doesn't match)
namespaces = {'ns': 'urn:iec62325.351:tc57wg16:451-6:balancingdocument:3:0'}

# Fetch and process imbalance volumes data from the existing zip file
def process_imbalance_volumes(zip_filepath='entsoe_response.zip'):
    # Extract the .zip file to access the XML content
    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
        extracted_files = zip_ref.namelist()
        xml_filename = extracted_files[0]  # Assuming there's only one file in the .zip
        zip_ref.extract(xml_filename)

    # Parse the XML content
    tree = ET.parse(xml_filename)
    root = tree.getroot()

    # Extract the namespace dynamically
    ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
    namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

    # Initialize lists to store parsed data
    timestamps_utc = []
    volumes = []

    # Iterate over all TimeSeries elements to differentiate by direction
    for timeseries in root.findall('ns:TimeSeries', namespaces):
        # Extract the direction information
        flow_direction_tag = timeseries.find('ns:flowDirection.direction', namespaces)
        
        if flow_direction_tag is not None:
            flow_direction = flow_direction_tag.text
        else:
            continue  # Skip this timeseries if no flow direction is found

        # Determine if the series is a deficit or an excedent based on flow direction
        if flow_direction == 'A02':  # A02 corresponds to deficit
            direction_sign = -1
        elif flow_direction == 'A01':  # A01 corresponds to excedent
            direction_sign = 1
        else:
            continue  # Skip if flow direction does not match either

        # Iterate over all Period elements within each TimeSeries
        for period in timeseries.findall('ns:Period', namespaces):
            start = period.find('ns:timeInterval/ns:start', namespaces)
            
            if start is None:
                continue

            start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

            # Iterate over all Point elements within each Period
            for point in period.findall('ns:Point', namespaces):
                position_tag = point.find('ns:position', namespaces)
                quantity_tag = point.find('ns:quantity', namespaces)

                # Handle missing tags with checks
                if position_tag is None or quantity_tag is None:
                    continue

                try:
                    position = int(position_tag.text)
                    quantity = float(quantity_tag.text) * direction_sign  # Apply direction sign here
                except ValueError as e:
                    print(f"Error converting position or quantity: {e}, skipping.")
                    continue

                # Calculate timestamp for the point based on position in UTC
                point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                
                # Append data to appropriate lists based on direction
                timestamps_utc.append(point_time_utc)
                volumes.append(quantity)

    # Create DataFrame from the parsed data
    df_imbalance = pd.DataFrame({
        'Timestamp_UTC': timestamps_utc,
        'Imbalance Volume': volumes
    })

    # Check if DataFrame is empty before further processing
    if df_imbalance.empty:
        print("DataFrame is empty after parsing XML. No data to process.")
        return df_imbalance

    # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
    df_imbalance['Timestamp_UTC'] = pd.to_datetime(df_imbalance['Timestamp_UTC'])
    df_imbalance['Timestamp_CET'] = df_imbalance['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

    # Remove the UTC column and rename CET to Timestamp for simplicity
    df_imbalance.drop(columns=['Timestamp_UTC'], inplace=True)
    df_imbalance.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Sort DataFrame by Timestamp to ensure it's ordered
    df_imbalance.sort_values(by='Timestamp', inplace=True)

    # Set up start and end dates dynamically for today (in CET)
    today = get_issue_date()
    start_of_day = pd.Timestamp(today.strftime('%Y-%m-%dT00:00:00'), tz='Europe/Berlin')
    end_of_day = pd.Timestamp(today.strftime('%Y-%m-%dT23:45:00'), tz='Europe/Berlin')

    # Filter Data to Keep Only the Relevant Day
    df_imbalance = df_imbalance[(df_imbalance['Timestamp'] >= start_of_day) & (df_imbalance['Timestamp'] <= end_of_day)]

    # Ensure all timestamps for the day are covered (assuming 15-min intervals)
    full_index_cet = pd.date_range(start=start_of_day, end=end_of_day + timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_imbalance = df_imbalance.set_index('Timestamp').reindex(full_index_cet, fill_value=0).rename_axis('Timestamp').reset_index()

    # **Shift All Timestamps One Interval Ahead**
    df_imbalance['Timestamp'] = df_imbalance['Timestamp'] + timedelta(minutes=15)

    # Adjusting the DataFrame to Include the Last Interval (`23:45 - 00:00`)
    final_end_time = pd.Timestamp(today.strftime('%Y-%m-%dT23:45:00'), tz='Europe/Berlin') + timedelta(minutes=15)
    df_imbalance = df_imbalance[(df_imbalance['Timestamp'] >= start_of_day + timedelta(minutes=15)) & (df_imbalance['Timestamp'] <= final_end_time)]

    # Clean up: remove the extracted files after processing
    os.remove(xml_filename)

    # Return the final DataFrame
    return df_imbalance

def fetch_imbalance_prices():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Define the endpoint and parameters for fetching imbalance prices via ENTSO-E API
    url = "https://web-api.tp.entsoe.eu/api"

    # Parameters for the API request
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A85",  # Document type for imbalance prices
        "controlArea_Domain": "10YRO-TEL------P",  # Romania's area EIC code
        "periodStart": start_cet.strftime('%Y%m%d%H%M'),  # Start date in yyyymmddhhmm format
        "periodEnd": end_cet.strftime('%Y%m%d%H%M'),  # End date in yyyymmddhhmm format
    }

    # Headers for the API request
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Save the response content to a file
        with open("./imbalance_prices_response.zip", "wb") as f:
            f.write(response.content)

        print("File saved as imbalance_prices_response.zip")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")

def process_imbalance_prices(zip_filepath='imbalance_prices_response.zip'):
    # Extract the .zip file to access the XML content
    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
        extracted_files = zip_ref.namelist()
        xml_filename = extracted_files[0]  # Assuming there's only one file in the .zip
        zip_ref.extract(xml_filename)

    # Parse the XML content
    tree = ET.parse(xml_filename)
    root = tree.getroot()

    # Extract the namespace dynamically if needed
    ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
    namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}
    print(f"Using namespace: {namespaces}")

    # Dictionary to store data by timestamp
    data_dict = {}

    # Iterate over all TimeSeries elements
    for timeseries_index, timeseries in enumerate(root.findall('ns:TimeSeries', namespaces)):
        # Extract Business Type
        business_type_tag = timeseries.find('ns:businessType', namespaces)

        if business_type_tag is None:
            print(f"[TimeSeries {timeseries_index}] No business type found, skipping...")
            continue

        business_type = business_type_tag.text
        print(f"[TimeSeries {timeseries_index}] Found Business Type: {business_type}")

        # Filter based on business type (only interested in imbalance prices A19)
        if business_type != "A19":
            print(f"[TimeSeries {timeseries_index}] Unknown Business Type: {business_type}, skipping...")
            continue

        # Iterate over all Period elements within each TimeSeries
        for period_index, period in enumerate(timeseries.findall('ns:Period', namespaces)):
            start = period.find('ns:timeInterval/ns:start', namespaces)

            if start is None:
                print(f"[TimeSeries {timeseries_index} - Period {period_index}] Period start time not found, skipping...")
                continue

            start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC
            print(f"[TimeSeries {timeseries_index} - Period {period_index}] Period start time (UTC): {start_time_utc}")

            # Iterate over all Point elements within each Period
            for point_index, point in enumerate(period.findall('ns:Point', namespaces)):
                position_tag = point.find('ns:position', namespaces)
                price_tag = point.find('ns:imbalance_Price.amount', namespaces)
                category_tag = point.find('ns:imbalance_Price.category', namespaces)

                # Handle missing tags with checks
                if position_tag is None or price_tag is None or category_tag is None:
                    print(f"[TimeSeries {timeseries_index} - Period {period_index} - Point {point_index}] Missing position, price, or category tag: {ET.tostring(point, encoding='unicode')}")
                    continue

                try:
                    position = int(position_tag.text)
                    price = float(price_tag.text)
                    category = category_tag.text
                    print(f"[TimeSeries {timeseries_index} - Period {period_index} - Point {point_index}] Extracted Point - Position: {position}, Price: {price}, Category: {category}")
                except ValueError as e:
                    print(f"Error converting position or price: {e}, skipping.")
                    continue

                # Calculate timestamp for the point based on position in UTC
                point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))

                # Store data in dictionary by timestamp
                if point_time_utc not in data_dict:
                    data_dict[point_time_utc] = {'Excedent Price': None, 'Deficit Price': None}

                if category == 'A04':  # Excedent Price
                    data_dict[point_time_utc]['Excedent Price'] = price
                elif category == 'A05':  # Deficit Price
                    data_dict[point_time_utc]['Deficit Price'] = price
                else:
                    print(f"Unknown category: {category}, skipping...")

    # Convert dictionary to DataFrame
    df_prices = pd.DataFrame.from_dict(data_dict, orient='index').reset_index()
    df_prices.rename(columns={'index': 'Timestamp_UTC'}, inplace=True)

    # Drop duplicate timestamps to avoid issues with reindexing
    df_prices['Timestamp_UTC'] = pd.to_datetime(df_prices['Timestamp_UTC'])
    df_prices = df_prices.drop_duplicates(subset='Timestamp_UTC', keep='first')

    # Check if DataFrame is empty before further processing
    if df_prices.empty:
        print("DataFrame is empty after parsing XML. No data to process.")
        return df_prices

    # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
    df_prices['Timestamp_CET'] = df_prices['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

    # Remove the UTC column and rename CET to Timestamp for simplicity
    df_prices.drop(columns=['Timestamp_UTC'], inplace=True)
    df_prices.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Sort DataFrame by Timestamp to ensure it's ordered
    df_prices.sort_values(by='Timestamp', inplace=True)

    # Filter Data to Keep Only the Relevant Day (adjust the date range accordingly)
    start_of_day = pd.Timestamp('2024-12-04T00:00:00', tz='Europe/Berlin')
    end_of_day = pd.Timestamp('2024-12-04T23:45:00', tz='Europe/Berlin')
    df_prices = df_prices[(df_prices['Timestamp'] >= start_of_day) & (df_prices['Timestamp'] <= end_of_day)]

    # Ensure all timestamps for the day are covered (assuming 15-min intervals)
    full_index_cet = pd.date_range(start=start_of_day, end=end_of_day + timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_prices = df_prices.set_index('Timestamp').reindex(full_index_cet).rename_axis('Timestamp').reset_index()

    # Clean up: remove the extracted files after processing
    os.remove(xml_filename)

    # Return the final DataFrame
    return df_prices

def create_combined_imbalance_dataframe(df_prices, df_volumes):
    """
    This function combines the imbalance prices and volumes into a single DataFrame.
    
    Parameters:
    df_prices (DataFrame): DataFrame containing timestamps, Excedent Price, and Deficit Price.
    df_volumes (DataFrame): DataFrame containing timestamps and Imbalance Volume.

    Returns:
    DataFrame: Combined DataFrame with Timestamp, Excedent Price, Deficit Price, and Imbalance Volume.
    """
    # Merge prices and volumes on the Timestamp column using an outer join to ensure all data is included.
    df_combined = pd.merge(df_prices, df_volumes, on='Timestamp', how='outer')

    # Sort the combined DataFrame by Timestamp to keep it in chronological order.
    df_combined = df_combined.sort_values(by='Timestamp')

    # Fill any missing values with 0.0 to ensure consistency in analysis.
    df_combined.fillna(0.0, inplace=True)

    return df_combined

#==========================================================================Wind Production==============================================================================
def fetch_process_wind_notified():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A69",  # Document type for wind and solar generation forecast
        "processType": "A18",   # A01 represents Day Ahead forecast
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B19"  # B19 corresponds to Wind Onshore
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)

                if start is None:
                    continue

                # Extract the start time and assume a 15-min resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_notified_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Notified Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_notified_production['Timestamp_UTC'] = pd.to_datetime(df_notified_production['Timestamp_UTC'])
        df_notified_production['Timestamp_CET'] = df_notified_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_notified_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_notified_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_notified_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_notified_production = df_notified_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: Start from 00:15 for intraday alignment
        df_notified_production['Timestamp'] = df_notified_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_notified_production['Notified Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_notified_production_excel = df_notified_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_notified_production_excel['Timestamp'] = df_notified_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_notified_production_excel.to_excel("./data_fetching/Entsoe/Notified_Production_Wind.xlsx", index=False)

        # Return the final DataFrame
        return df_notified_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_wind_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B19"  # B19 corresponds to Wind Onshore
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_actual_production_excel = df_actual_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_production_excel['Timestamp'] = df_actual_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_production_excel.to_excel("./data_fetching/Entsoe/Actual_Production_Wind.xlsx", index=False)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def combine_wind_production_data(df_notified, df_actual, df_forecast):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_notified, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Notified Production (MW)', 'Actual Production (MW)']

    # Step 5: Add the volue forecast to the combined dataframe by aligning timestamps
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')

    # Aligning the forecast data based on the timestamps in the combined dataframe
    df_combined = df_combined.set_index('Timestamp')
    df_combined['Volue Forecast (MW)'] = df_forecast['Volue Forecast (MW)']
    df_combined.reset_index(inplace=True)

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

def fetch_volue_wind_data():
    # INSTANCES curve 15 min
    today = get_issue_date()
    curve = session.get_curve(name='pro ro wnd ec00 mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    df_wind_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_wind_15min = df_wind_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_wind_15min

def fetch_volue_wind_data_15min():
    # INSTANCES curve 15 min
    today = get_issue_date()
    curve = session.get_curve(name='pro ro wnd intraday lastec mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    df_wind_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_wind_15min = df_wind_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_wind_15min
    
# Adjusting the Volue forecast DataFrame
def preprocess_volue_forecast(df_forecast):
    # Assuming the forecast dataframe is using the index as the timestamp
    # Reset the index to make it a proper column
    df_forecast.reset_index(inplace=True)

    # Rename the index column to 'Timestamp' for consistency with other dataframes
    df_forecast.rename(columns={'index': 'Timestamp'}, inplace=True)
    # Step 5: Rename the forecast column to match our desired name
    df_forecast.rename(columns={df_forecast.columns[-1]: 'Volue Forecast (MW)'}, inplace=True)

    # Sort by timestamp to align with the other dataframes
    df_forecast = df_forecast.sort_values(by='Timestamp')

    # Shift all timestamps forward by 1 hour
    df_forecast['Timestamp'] = pd.to_datetime(df_forecast['Timestamp'])

    return df_forecast

# Solcast Forecast======================================
def fetching_Cogealac_data_15min():
    lat = 44.561156
    lon = 28.562586
    # Fetch data from the API
    api_url = "https://api.solcast.com.au/data/forecast/radiation_and_weather?latitude={}&longitude={}&hours=168&output_parameters=air_temp,wind_direction_100m,wind_direction_10m,wind_speed_100m,wind_speed_10m&period=PT15M&format=csv&api_key={}".format(lat, lon, solcast_api_key)
    response = requests.get(api_url)
    print("Fetching data...")
    if response.status_code == 200:
        # Write the content to a CSV file
        with open("./data_fetching/Solcast/Wind_dataset_raw_15min.csv", 'wb') as file:
            file.write(response.content)
    else:
        print(response.text)  # Add this line to see the error message returned by the API
        raise Exception(f"Failed to fetch data: Status code {response.status_code}")

def predicting_wind_production_15min():
    # Loading the input dataset
    data = pd.read_csv("./data_fetching/Solcast/Wind_dataset_raw_15min.csv")
    forecast_dataset = pd.read_excel("./Wind_Production_Forecast/Input_Wind_dataset_15min.xlsx")

    data['period_end'] = pd.to_datetime(data['period_end'], errors='coerce', format='%Y-%m-%dT%H:%M:%SZ')
    # Shift the 'period_end' column by 2 hours
    data['period_end'] = data['period_end'] + pd.Timedelta(hours=1)
    forecast_dataset['Data'] = data.period_end.dt.strftime('%Y-%m-%d').values
    # Creating the Interval column
    forecast_dataset['Interval'] = data.period_end.dt.hour * 4 + data.period_end.dt.minute // 15 + 1
    # Replace NaNs in the 'Interval' column with 0
    forecast_dataset['Interval'].fillna(9, inplace=True)
    # Replace NaNs in the 'Date' column with the previous valid observation
    forecast_dataset['Data'].fillna(method='ffill', inplace=True)
    # Completing the wind_direction_100m column
    forecast_dataset["wind_direction_100m"] = data["wind_direction_100m"].values
    # Completing the wind_direction_10m column
    forecast_dataset["wind_direction_10m"] = data["wind_direction_10m"].values
    # Completing the wind_speed_100m column
    forecast_dataset["wind_speed_100m"] = data["wind_speed_100m"].values
    # Completing the wind_speed_10m column
    forecast_dataset["wind_speed_10m"] = data["wind_speed_10m"].values
    # Completing the temperature column
    forecast_dataset["temperature"] = data["air_temp"].values

    xgb_loaded = joblib.load("./Wind_Production_Forecast/rs_xgb_wind_production_quarterly_1024.pkl")

    forecast_dataset["Month"] = pd.to_datetime(forecast_dataset.Data).dt.month
    dataset = forecast_dataset.copy()
    forecast_dataset = forecast_dataset.drop("Data", axis=1)
    forecast_dataset = forecast_dataset[["Interval", "wind_direction_100m", "wind_direction_10m", "wind_speed_100m", "wind_speed_10m", "temperature", "Month"]]
    preds = xgb_loaded.predict(forecast_dataset.values)

    # Rounding each value in the list to the third decimal
    rounded_values = [round(value, 3) for value in preds]

    #Exporting Results to Excel
    workbook = xlsxwriter.Workbook("./Wind_Production_Forecast/Wind_Forecast_Production_15min.xlsx")
    worksheet = workbook.add_worksheet("Production_Predictions")
    date_format = workbook.add_format({'num_format':'dd.mm.yyyy'})
    # Define a format for cells with three decimal places
    decimal_format = workbook.add_format({'num_format': '0.000'})
    row = 1
    col = 0
    worksheet.write(0,0,"Data")
    worksheet.write(0,1,"Interval")
    worksheet.write(0,2,"Prediction")

    for value in rounded_values:
        worksheet.write(row, col + 2, value, decimal_format)
        row += 1
    row = 1
    for Data, Interval in zip(dataset.Data, dataset.Interval):
        worksheet.write(row, col + 0, Data, date_format)
        worksheet.write(row, col + 1, Interval)
        row += 1

    workbook.close()

    # Create Timestamp column
    dataset['Timestamp'] = dataset.apply(
        lambda row: pd.Timestamp(f"{row['Data']}") + pd.Timedelta(minutes=15 * (row['Interval'] - 1)),
        axis=1
    )
    dataset['Timestamp'] = dataset['Timestamp'].dt.tz_localize('CET')

    # Add predictions to the dataset
    dataset['Prediction (MW)'] = rounded_values

    # Select the relevant columns to return
    final_forecast_df = dataset[['Timestamp', 'Prediction (MW)']]

    return final_forecast_df

def add_solcast_forecast_to_wind_dataframe(df_combined, df_solcast_forecast):
    # Ensure both dataframes are sorted and indexed properly by 'Timestamp'
    df_combined = df_combined.sort_values(by='Timestamp').set_index('Timestamp')

    # Convert the Timestamp in solcast forecast to datetime and set appropriate timezone
    df_solcast_forecast['Timestamp'] = pd.to_datetime(df_solcast_forecast['Timestamp'], utc=True).dt.tz_convert('Europe/Berlin')

    # Set the Timestamp as the index for reindexing purposes
    df_solcast_forecast = df_solcast_forecast.set_index('Timestamp')

    # Remove any duplicates in solcast forecast index to avoid reindexing issues
    df_solcast_forecast = df_solcast_forecast[~df_solcast_forecast.index.duplicated(keep='first')]

    # Check and correct the column name for forecast values
    print("Columns in Solcast Forecast DataFrame:", df_solcast_forecast.columns)

    # Replace 'Solcast_Forecast' with the correct column name if necessary
    forecast_column = 'Prediction (MW)'  # Replace with correct name if different
    if forecast_column not in df_solcast_forecast.columns:
        raise KeyError(f"Column '{forecast_column}' not found in df_solcast_forecast. Available columns: {df_solcast_forecast.columns}")

    # Align the Solcast dataframe with the full index of the combined wind dataframe
    full_index = df_combined.index.union(df_solcast_forecast.index).sort_values()

    # Remove duplicate labels in the full index as well to ensure uniqueness
    full_index = full_index.drop_duplicates()

    # Reindex the solcast forecast DataFrame
    df_solcast_forecast = df_solcast_forecast.reindex(full_index).fillna(method='ffill')

    # Remove any existing name from the index to prevent duplication during reset_index
    df_solcast_forecast.index.name = None

    # Reset index to make Timestamp a column again
    df_solcast_forecast.reset_index(inplace=True)

    # Keep only the relevant timestamps that match with the combined dataframe
    df_solcast_forecast = df_solcast_forecast[df_solcast_forecast['index'].isin(df_combined.index)]

    # Rename 'index' to 'Timestamp' for clarity
    df_solcast_forecast.rename(columns={'index': 'Timestamp'}, inplace=True)

    # Add the Solcast forecast to the combined wind dataframe
    df_combined = df_combined.reset_index()
    df_combined['Solcast Forecast (MW)'] = df_solcast_forecast.set_index('Timestamp')[forecast_column].reindex(df_combined['Timestamp']).values

    return df_combined

#==========================================================================Solar Production==============================================================================
def fetch_process_solar_notified():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A69",  # Document type for wind and solar generation forecast
        "processType": "A18",   # A01 represents Day Ahead forecast
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B16"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)

                if start is None:
                    continue

                # Extract the start time and assume a 15-min resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_notified_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Notified Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_notified_production['Timestamp_UTC'] = pd.to_datetime(df_notified_production['Timestamp_UTC'])
        df_notified_production['Timestamp_CET'] = df_notified_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_notified_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_notified_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_notified_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_notified_production = df_notified_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: Start from 00:15 for intraday alignment
        df_notified_production['Timestamp'] = df_notified_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_notified_production['Notified Production (MW)'].fillna(0, inplace=True)

         # Writing the Notified Production to Excel
        df_notified_production_excel = df_notified_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_notified_production_excel['Timestamp'] = df_notified_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_notified_production_excel.to_excel("./data_fetching/Entsoe/Notified_Production_Solar.xlsx", index=False)

        # Return the final DataFrame
        return df_notified_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_solar_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B16"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Writing the Notified Production to Excel
        df_actual_production_excel = df_actual_production.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_production_excel['Timestamp'] = df_actual_production_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_production_excel.to_excel("./data_fetching/Entsoe/Actual_Production_Solar.xlsx", index=False)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_volue_solar_data():
    today = get_issue_date()
    # INSTANCE curve 15 min
    curve = session.get_curve(name='pro ro spv ec00 mwh/h cet min15 f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_15min = curve.get_instance(issue_date=today)
    pd_s_15min = ts_15min.to_pandas() # convert TS object to pandas.Series object
    df_solar_15min = pd_s_15min.to_frame() # convert pandas.Series to pandas.DataFrame
    return df_solar_15min

def combine_solar_production_data(df_notified, df_actual, df_forecast):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_notified, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Notified Production (MW)', 'Actual Production (MW)']

    # Step 5: Add the volue forecast to the combined dataframe by aligning timestamps
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')

    # Aligning the forecast data based on the timestamps in the combined dataframe
    df_combined = df_combined.set_index('Timestamp')
    df_combined['Volue Forecast (MW)'] = df_forecast['Volue Forecast (MW)']
    df_combined.reset_index(inplace=True)

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

#==========================================================================Hydro Production==============================================================================

def fetch_process_hydro_water_reservoir_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B12"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_process_hydro_river_actual_production():
    # Setting up the start and end dates (today for intraday)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Format the start and end dates to match the API requirements (yyyymmddhhmm)
    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    
    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual API key
        "documentType": "A75",  # Document type for actual generation per type (all production types)
        "processType": "A16",   # A16 represents Realized generation
        "in_Domain": "10YRO-TEL------P",  # EIC code for the desired country/region (Romania in this case)
        "periodStart": period_start,  # Start date in yyyymmddhhmm format
        "periodEnd": period_end,      # End date in yyyymmddhhmm format
        "PsrType": "B11"  # B19 corresponds to Solar
    }
    
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }
    
    # Make the request to the ENTSO-E API
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        # Parse the XML response
        root = ET.fromstring(response.content)

        # Extract namespace dynamically
        ns_match = root.tag[root.tag.find("{"):root.tag.find("}")+1]
        namespaces = {'ns': ns_match.strip("{}")} if ns_match else {}

        # Initialize lists to store parsed data
        timestamps_utc = []
        quantities = []

        # Iterate over TimeSeries to extract the points
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            # Iterate over each Period in TimeSeries
            for period in timeseries.findall('ns:Period', namespaces):
                start = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start is None or resolution is None:
                    continue

                # Extract the start time and resolution
                start_time_utc = datetime.strptime(start.text, '%Y-%m-%dT%H:%MZ')  # Start time is in UTC

                # Iterate over all Point elements within each Period
                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    # Handle missing tags with checks
                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    # Calculate timestamp for the point based on position in UTC
                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    
                    # Append data to lists
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame from the parsed data
        df_actual_production = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Production (MW)': quantities
        })

        # Convert the UTC timestamps to CET (Europe/Berlin, which handles DST)
        df_actual_production['Timestamp_UTC'] = pd.to_datetime(df_actual_production['Timestamp_UTC'])
        df_actual_production['Timestamp_CET'] = df_actual_production['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')

        # Remove the UTC column and rename CET to Timestamp for simplicity
        df_actual_production.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual_production.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Sort DataFrame by Timestamp to ensure it's ordered
        df_actual_production.sort_values(by='Timestamp', inplace=True)

        # Ensure all timestamps for the day are covered (assuming 15-min intervals)
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        full_index_cet = pd.date_range(start=start_of_day, end=end_of_day, freq='15T', tz='Europe/Berlin')

        # Set index and reindex to ensure completeness, handling missing intervals
        df_actual_production = df_actual_production.set_index('Timestamp').reindex(full_index_cet, fill_value=np.nan).rename_axis('Timestamp').reset_index()

        # **Shift All Timestamps One Interval Ahead**: This should align it with notified values
        df_actual_production['Timestamp'] = df_actual_production['Timestamp'] + timedelta(minutes=15)

        # Replace NaNs with zeros after reindexing to maintain a complete timeline
        df_actual_production['Actual Production (MW)'].fillna(0, inplace=True)

        # Return the final DataFrame
        return df_actual_production

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_volue_hydro_data():
    today = get_issue_date()
    # INSTANCE curve hour
    curve = session.get_curve(name='pro ro hydro tot mwh/h cet h f')
    # INSTANCES curves contain a timeseries for each defined issue dates
    # Get a list of available curves with issue dates within a timerange with:
    # curve.search_instances(issue_date_from='2018-01-01', issue_date_to='2018-01-01')
    ts_h = curve.get_instance(issue_date=today)
    pd_s_h = ts_h.to_pandas() # convert TS object to pandas.Series object
    pd_df_h = pd_s_h.to_frame() # convert pandas.Series to pandas.DataFrame

    return pd_df_h

def align_and_combine_hydro_data(df_notified, df_actual, df_volue_forecast):
    # Ensure notified and actual dataframes are sorted and set the index to Timestamp
    df_notified = df_notified.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')

    # Load volue forecast data and rename the columns appropriately
    df_volue_forecast = df_volue_forecast.rename(columns={df_volue_forecast.columns[0]: 'Volue Forecast (MW)'})
    
    # Convert Volue forecast index to datetime and set the timezone to CET (Europe/Berlin)
    df_volue_forecast.index = pd.to_datetime(df_volue_forecast.index, utc=True).tz_convert('Europe/Berlin')
    
    # Resample the volue forecast to 15-minute intervals using linear interpolation
    df_volue_forecast_resampled = df_volue_forecast.resample('15T').interpolate(method='linear')
    
    # Reset the index of the resampled forecast and rename the index to Timestamp
    df_volue_forecast_resampled = df_volue_forecast_resampled.reset_index().rename(columns={'index': 'Timestamp'})

    # Align the resampled volue forecast DataFrame to the combined notified and actual hydro production data
    df_combined = pd.concat([df_notified, df_actual], axis=1).reset_index()
    df_combined['Volue Forecast (MW)'] = df_volue_forecast_resampled.set_index('Timestamp')['Volue Forecast (MW)'].reindex(df_combined['Timestamp']).values

    # Sort the final DataFrame by Timestamp and fill any NaN values with zeros
    df_combined.sort_values(by='Timestamp', inplace=True)
    df_combined.fillna(0, inplace=True)

    # Rename columns for clarity
    df_combined.columns = ['Timestamp', 'Hydro Reservoir Actual (MW)', 'Hydro River Actual (MW)', 'Volue Forecast (MW)']
    df_combined["Hydro Actual (MW)"] = df_combined["Hydro Reservoir Actual (MW)"] + df_combined["Hydro River Actual (MW)"]
    # Writing the Hydro Production to Excel
    df_combined_excel = df_combined.copy()
    # Remove timezone information from the 'Timestamp' column
    df_combined_excel['Timestamp'] = df_combined_excel['Timestamp'].dt.tz_localize(None)
    # Save to Excel
    df_combined_excel.to_excel("./data_fetching/Entsoe/Hydro_Production.xlsx", index=False)
    return df_combined

# Fetch and process both datasets
# df_wind_notified = fetch_process_wind_notified()
# df_wind_actual = fetch_process_wind_actual_production()
# df_wind_volue_forecast = preprocess_volue_forecast(fetch_volue_wind_data())

# df_wind_production = combine_wind_production_data(df_wind_notified, df_wind_actual, df_wind_volue_forecast)
# st.dataframe(df_wind_production)

# fetching_Cogealac_data_15min()
# df_wind_solcast_forecast = predicting_wind_production_15min()
# st.dataframe(df_wind_solcast_forecast)

# df_wind_final = add_solcast_forecast_to_wind_dataframe(df_wind_production, df_wind_solcast_forecast)
# st.dataframe(df_wind_final)

# df_solar_notified = fetch_process_solar_notified()
# df_solar_actual = fetch_process_solar_actual_production()
# df_solar = combine_solar_production_data(df_solar_notified, df_solar_actual)
# st.dataframe(df_solar)

# df_hydro_reservoir_actual = fetch_process_hydro_water_reservoir_actual_production()
# df_hydro_river_actual = fetch_process_hydro_river_actual_production()
# df_hydro_volue = fetch_volue_hydro_data()
# df_hydro = align_and_combine_hydro_data(df_hydro_reservoir_actual, df_hydro_river_actual, df_hydro_volue)

# st.dataframe(df_hydro)

#==========================================================================Consumption==============================================================================
def fetch_consumption_forecast():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-1)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A65",
        "processType": "A01",  # For notified consumption
        "outBiddingZone_Domain": "10YRO-TEL------P",
        "periodStart": period_start,  # Start period as per Postman
        "periodEnd": period_end    # End period as per Postman
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
       
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        quantities = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame
        df_forecast = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Forecasted Consumption (MW)': quantities
        })

        df_forecast['Timestamp_UTC'] = pd.to_datetime(df_forecast['Timestamp_UTC'])
        df_forecast['Timestamp_CET'] = df_forecast['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_forecast.drop(columns=['Timestamp_UTC'], inplace=True)
        df_forecast.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Slice to intraday values
        start_of_day = pd.Timestamp(today, tz='Europe/Berlin')
        end_of_day = pd.Timestamp(today + timedelta(days=1), tz='Europe/Berlin') - timedelta(minutes=15)
        df_forecast = df_forecast[(df_forecast['Timestamp'] >= start_of_day) & (df_forecast['Timestamp'] <= end_of_day)]

        # Writing the Consumption Forecast to Excel
        df_forecast_excel = df_forecast.copy()
        # Remove timezone information from the 'Timestamp' column
        df_forecast_excel['Timestamp'] = df_forecast_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_forecast_excel.to_excel("./data_fetching/Entsoe/Consumption_Forecast.xlsx", index=False)

        return df_forecast

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def fetch_actual_consumption():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A65",  # Document type for actual load
        "processType": "A16",   # A16 represents Realized consumption
        "outBiddingZone_Domain": "10YRO-TEL------P",  # EIC code for Romania
        "periodStart": period_start,
        "periodEnd": period_end,
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        quantities = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        quantity = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    quantities.append(quantity)

        # Create DataFrame
        df_actual = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Actual Consumption (MW)': quantities
        })

        df_actual['Timestamp_UTC'] = pd.to_datetime(df_actual['Timestamp_UTC'])
        df_actual['Timestamp_CET'] = df_actual['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_actual.drop(columns=['Timestamp_UTC'], inplace=True)
        df_actual.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Writing the Consumption Actual to Excel
        df_actual_excel = df_actual.copy()
        # Remove timezone information from the 'Timestamp' column
        df_actual_excel['Timestamp'] = df_actual_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        df_actual_excel.to_excel("./data_fetching/Entsoe/Consumption_Actual.xlsx", index=False)

        return df_actual

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

def combine_consumption_data(df_forecast, df_actual):
    # Step 1: Sort and align the wind notified and actual production DataFrames
    df_forecast = df_forecast.sort_values(by='Timestamp').set_index('Timestamp')
    df_actual = df_actual.sort_values(by='Timestamp').set_index('Timestamp')
    
    # Step 2: Concatenate the notified and actual data based on Timestamp
    df_combined = pd.concat([df_forecast, df_actual], axis=1)

    # Step 3: Reset index to make Timestamp a column again
    df_combined.reset_index(inplace=True)
    
    # Step 4: Rename the columns for better readability
    df_combined.columns = ['Timestamp', 'Consumption Forecast (MW)', 'Actual Consumption (MW)']

    # Step 6: Fill any missing values if required (e.g., with 0 or 'NaN')
    # df_combined.fillna(method='ffill', inplace=True)  # Forward fill any missing values if necessary

    return df_combined

#==========================================================================CrossBorder Flaws==================================================================

#1.a) RO_BG Physical Flows========================================================================================================

def fetch_physical_flows_bulgaria_to_romania():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": "10YCA-BULGARIA-R",  # Bulgaria's EIC code
        "in_Domain": "10YRO-TEL------P",  # Romania's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def fetch_physical_flows_romania_to_bulgaria():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": "10YRO-TEL------P",  # Romania's EIC code
        "in_Domain": "10YCA-BULGARIA-R",  # Bulgaria's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def concatenate_cross_border_flows(df_bg_to_ro, df_ro_to_bg):
    # Rename columns for clarity before concatenation
    df_bg_to_ro = df_bg_to_ro.rename(columns={"Physical Flow (MW)": "BG → RO Flow (MW)"})
    df_ro_to_bg = df_ro_to_bg.rename(columns={"Physical Flow (MW)": "RO → BG Flow (MW)"})

    # Merge dataframes on 'Timestamp'
    df_cross_border_flows = pd.merge(df_bg_to_ro, df_ro_to_bg, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure correct chronological order
    df_cross_border_flows = df_cross_border_flows.sort_values(by='Timestamp').reset_index(drop=True)

    return df_cross_border_flows

#1.b) RO_BG Crossborder Schedule===================================================================================================

eic_Bulgaria = "10YCA-BULGARIA-R"

def process_entsoe_response(response, start_cet, end_cet):
    """ Process ENTSO-E XML response and return a DataFrame with scheduled flows. """
    
    root = ET.fromstring(response.content)  # Ensure response is correctly passed
    namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

    timestamps_utc = []
    flows = []

    for timeseries in root.findall('ns:TimeSeries', namespaces):
        for period in timeseries.findall('ns:Period', namespaces):
            start_time = period.find('ns:timeInterval/ns:start', namespaces)
            resolution = period.find('ns:resolution', namespaces)

            if start_time is None or resolution is None:
                continue

            start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

            position_data = {}  # Store extracted position data

            for point in period.findall('ns:Point', namespaces):
                position_tag = point.find('ns:position', namespaces)
                quantity_tag = point.find('ns:quantity', namespaces)

                if position_tag is None or quantity_tag is None:
                    continue

                try:
                    position = int(position_tag.text)
                    flow = float(quantity_tag.text)
                    position_data[position] = flow  # Store extracted flow data
                except ValueError as e:
                    print(f"Error converting position or quantity: {e}, skipping.")
                    continue

            # Ensure we have a full 24-hour schedule by filling missing positions
            for position in range(1, 25):  # 1 to 24 positions (1-based index)
                if position not in position_data:
                    if position > 1:
                        # Forward-fill from previous hour
                        position_data[position] = position_data.get(position - 1, 0)
                    else:
                        # Default to 0 for first position if no previous value
                        position_data[position] = 0

            # Generate timestamps and match extracted data
            for position, flow in position_data.items():
                point_time_utc = start_time_utc + timedelta(hours=(position - 1))
                timestamps_utc.append(point_time_utc)
                flows.append(flow)

    df_schedule = pd.DataFrame({'Timestamp_UTC': timestamps_utc, 'Scheduled Flow (MW)': flows})

    if df_schedule.empty:
        print("No data found, returning a dataframe with 0 values.")
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        return pd.DataFrame({'Timestamp': full_index, 'Scheduled Flow (MW)': 0})

    df_schedule['Timestamp_UTC'] = pd.to_datetime(df_schedule['Timestamp_UTC'])
    df_schedule['Timestamp_CET'] = df_schedule['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
    df_schedule.drop(columns=['Timestamp_UTC'], inplace=True)
    df_schedule.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

    # Generate full quarter-hourly timestamps for CET timezone
    full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_schedule = df_schedule.set_index('Timestamp').reindex(full_index).reset_index()
    df_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

    df_schedule['Scheduled Flow (MW)'] = df_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(0)

    return df_schedule

def fetch_cross_border_schedule(out_domain, in_domain):
    """
    Fetches the cross-border scheduled flow from ENTSO-E API, processes it, and returns a cleaned DataFrame.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    # Convert to UTC for API request
    start_utc = start_cet.tz_convert('UTC') - timedelta(hours=2)
    period_start = start_utc.strftime('%Y%m%d%H%M')
    period_end = end_cet.tz_convert('UTC').strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"
    params = {
        "securityToken": api_key_entsoe,
        "documentType": "A09",
        "out_Domain": out_domain,
        "in_Domain": in_domain,
        "periodStart": period_start,
        "periodEnd": period_end,
        "contract_MarketAgreement.Type": "A05"
    }
    headers = {"Content-Type": "application/xml", "Accept": "application/xml"}

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return process_entsoe_response(response, start_cet, end_cet)
    
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        return create_zeroed_crossborder_schedule(start_cet, end_cet)

# Function to create a DataFrame filled with zeros if no data is available
def create_zeroed_crossborder_schedule(start_cet, end_cet):
    timestamps = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    return pd.DataFrame({'Timestamp': timestamps, 'Scheduled Flow (MW)': [0] * len(timestamps)})

# Function to convert hourly data to quarter-hourly intervals
def expand_to_quarterly_intervals(df_schedule, start_cet, end_cet):
    expanded_data = []
    for _, row in df_schedule.iterrows():
        timestamp = row['Timestamp']
        value = row['Scheduled Flow (MW)']
        for i in range(4):  # 4 intervals per hour
            expanded_data.append({'Timestamp': timestamp + pd.Timedelta(minutes=15 * i), 'Scheduled Flow (MW)': value})

    df_quarterly_schedule = pd.DataFrame(expanded_data)
    full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
    df_quarterly_schedule = df_quarterly_schedule.set_index('Timestamp').reindex(full_index).reset_index()
    df_quarterly_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)
    df_quarterly_schedule['Scheduled Flow (MW)'] = df_quarterly_schedule['Scheduled Flow (MW)'].fillna(0)

    return df_quarterly_schedule

def fetch_cross_border_schedule_with_fallback(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    # Fetch starting from the last UTC hour of the previous day
    start_utc = start_cet.tz_convert('UTC') - timedelta(hours=1)
    period_start = start_utc.strftime('%Y%m%d%H%M')
    period_end = end_cet.tz_convert('UTC').strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": out_domain,  # Outgoing country EIC code
        "in_Domain": in_domain,   # Incoming country EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end,  # End period
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(hours=(position - 1))
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Handle cases with no data or single point
        if not timestamps_utc:
            print("No hourly data in the response. Creating fallback DataFrame with zeros.")
            hourly_index = pd.date_range(start=start_cet, end=end_cet - timedelta(hours=1), freq='H', tz='Europe/Berlin')
            df_hourly = pd.DataFrame({'Timestamp': hourly_index, 'Scheduled Flow (MW)': 0})
        else:
            # Create hourly DataFrame
            df_schedule = pd.DataFrame({
                'Timestamp_UTC': timestamps_utc,
                'Scheduled Flow (MW)': flows
            })

            df_schedule['Timestamp_UTC'] = pd.to_datetime(df_schedule['Timestamp_UTC'])
            df_schedule['Timestamp_CET'] = df_schedule['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
            df_schedule.drop(columns=['Timestamp_UTC'], inplace=True)
            df_schedule.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

            # Filter for the current day in CET (00:00 to 23:45)
            df_schedule = df_schedule[(df_schedule['Timestamp'] >= start_cet) & (df_schedule['Timestamp'] < end_cet)]

            # Extrapolate hourly data to quarterly intervals
            expanded_data = []
            for index, row in df_schedule.iterrows():
                timestamp = row['Timestamp']
                value = row['Scheduled Flow (MW)']

                for i in range(4):
                    expanded_data.append({
                        'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                        'Scheduled Flow (MW)': value
                    })

            df_quarterly_schedule = pd.DataFrame(expanded_data)

            # Ensure complete index for intraday
            full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
            df_quarterly_schedule = df_quarterly_schedule.set_index('Timestamp').reindex(full_index).reset_index()
            df_quarterly_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

            # Forward-fill and backward-fill missing values
            df_quarterly_schedule['Scheduled Flow (MW)'] = df_quarterly_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(method='bfill')

            return df_quarterly_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        # Create fallback DataFrame with zeros
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        return pd.DataFrame({'Timestamp': full_index, 'Scheduled Flow (MW)': 0})

#1.c) Combining the Flows and Schedules into a dataframe=============================================================================
def combine_physical_and_scheduled_flows_ro_bg(df_cross_border_flows, df_bg_to_ro_schedule, df_ro_to_bg_schedule):
    # Rename schedule columns for clarity
    df_bg_to_ro_schedule = df_bg_to_ro_schedule.rename(columns={"Scheduled Flow (MW)": "BG → RO Scheduled Flow (MW)"})
    df_ro_to_bg_schedule = df_ro_to_bg_schedule.rename(columns={"Scheduled Flow (MW)": "RO → BG Scheduled Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_cross_border_flows, df_bg_to_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_to_bg_schedule, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

#2. RO_RS Flows========================================================================================================

def fetch_physical_flows(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest') + timedelta(hours=-2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": out_domain,  # Other's EIC code
        "in_Domain": in_domain,  # Inner's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=60 * (position - 1))  # Hourly data
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create hourly DataFrame
        df_hourly_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_hourly_flows['Timestamp_UTC'] = pd.to_datetime(df_hourly_flows['Timestamp_UTC'])
        df_hourly_flows['Timestamp_CET'] = df_hourly_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_hourly_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_hourly_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        # Extrapolate hourly data to quarterly intervals
        expanded_data = []
        for index, row in df_hourly_flows.iterrows():
            timestamp = row['Timestamp']
            value = row['Physical Flow (MW)']

            for i in range(4):
                expanded_data.append({
                    'Timestamp': timestamp + pd.Timedelta(minutes=15 * i),
                    'Physical Flow (MW)': value
                })

        df_quarterly_flows = pd.DataFrame(expanded_data)
        df_quarterly_flows = df_quarterly_flows.sort_values(by='Timestamp').reset_index(drop=True)

        return df_quarterly_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def combine_physical_and_scheduled_flows_ro_rs(df_rs_ro_flow, df_ro_rs_flow, df_rs_ro_schedule, df_ro_rs_schedule):
    # Rename schedule columns for clarity
    df_ro_rs_schedule = df_ro_rs_schedule.rename(columns={"Scheduled Flow (MW)": "RO → RS Scheduled Flow (MW)"})
    df_rs_ro_schedule = df_rs_ro_schedule.rename(columns={"Scheduled Flow (MW)": "RS → RO Scheduled Flow (MW)"})
    df_ro_rs_flow = df_ro_rs_flow.rename(columns={"Physical Flow (MW)": "RO → RS Physical Flow (MW)"})
    df_rs_ro_flow = df_rs_ro_flow.rename(columns={"Physical Flow (MW)": "RS → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_rs_schedule, df_rs_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_rs_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_rs_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Serbia = "10YCS-SERBIATSOV"
eic_Romania = "10YRO-TEL------P"

#3. RO_UA Flaws========================================================================================================

def combine_physical_and_scheduled_flows_ro_ua(df_ua_ro_flow, df_ro_ua_flow, df_ua_ro_schedule, df_ro_ua_schedule):
    # Rename schedule columns for clarity
    df_ro_ua_schedule = df_ro_ua_schedule.rename(columns={"Scheduled Flow (MW)": "RO → UA Scheduled Flow (MW)"})
    df_ua_ro_schedule = df_ua_ro_schedule.rename(columns={"Scheduled Flow (MW)": "UA → RO Scheduled Flow (MW)"})
    df_ro_ua_flow = df_ro_ua_flow.rename(columns={"Physical Flow (MW)": "RO → UA Physical Flow (MW)"})
    df_ua_ro_flow = df_ua_ro_flow.rename(columns={"Physical Flow (MW)": "UA → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_ua_schedule, df_ua_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_ua_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ua_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Ukraine = "10Y1001C--000182"

#4. RO_MD Flows========================================================================================================

def combine_physical_and_scheduled_flows_ro_md(df_md_ro_flow, df_ro_md_flow, df_md_ro_schedule, df_ro_md_schedule):
    # Rename schedule columns for clarity
    df_ro_md_schedule = df_ro_md_schedule.rename(columns={"Scheduled Flow (MW)": "RO → MD Scheduled Flow (MW)"})
    df_md_ro_schedule = df_md_ro_schedule.rename(columns={"Scheduled Flow (MW)": "MD → RO Scheduled Flow (MW)"})
    df_ro_md_flow = df_ro_md_flow.rename(columns={"Physical Flow (MW)": "RO → MD Physical Flow (MW)"})
    df_md_ro_flow = df_md_ro_flow.rename(columns={"Physical Flow (MW)": "MD → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_md_schedule, df_md_ro_schedule, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_md_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_md_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Moldova = "10Y1001A1001A990"

#5. RO_HU Flows========================================================================================================

def fetch_physical_flows_ro_hu(out_domain, in_domain):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin') - timedelta(hours=2)
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = start_cet.strftime('%Y%m%d%H%M')
    period_end = end_cet.strftime('%Y%m%d%H%M')

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A11",  # Aggregated energy data report
        "out_Domain": out_domain,  # Hungary's EIC code
        "in_Domain": in_domain,  # Romania's EIC code
        "periodStart": period_start,  # Start period
        "periodEnd": period_end  # End period
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        timestamps_utc = []
        flows = []

        # Extract values from XML
        for timeseries in root.findall('ns:TimeSeries', namespaces):
            for period in timeseries.findall('ns:Period', namespaces):
                start_time = period.find('ns:timeInterval/ns:start', namespaces)
                resolution = period.find('ns:resolution', namespaces)

                if start_time is None or resolution is None:
                    continue

                start_time_utc = datetime.strptime(start_time.text, '%Y-%m-%dT%H:%MZ')

                for point in period.findall('ns:Point', namespaces):
                    position_tag = point.find('ns:position', namespaces)
                    quantity_tag = point.find('ns:quantity', namespaces)

                    if position_tag is None or quantity_tag is None:
                        continue

                    try:
                        position = int(position_tag.text)
                        flow = float(quantity_tag.text)
                    except ValueError as e:
                        print(f"Error converting position or quantity: {e}, skipping.")
                        continue

                    point_time_utc = start_time_utc + timedelta(minutes=15 * (position - 1))
                    timestamps_utc.append(point_time_utc)
                    flows.append(flow)

        # Create DataFrame
        df_physical_flows = pd.DataFrame({
            'Timestamp_UTC': timestamps_utc,
            'Physical Flow (MW)': flows
        })

        df_physical_flows['Timestamp_UTC'] = pd.to_datetime(df_physical_flows['Timestamp_UTC'])
        df_physical_flows['Timestamp_CET'] = df_physical_flows['Timestamp_UTC'].dt.tz_localize('UTC').dt.tz_convert('Europe/Berlin')
        df_physical_flows.drop(columns=['Timestamp_UTC'], inplace=True)
        df_physical_flows.rename(columns={'Timestamp_CET': 'Timestamp'}, inplace=True)

        return df_physical_flows

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Physical Flow (MW)"])  # Return empty DataFrame on failure

def fetch_cross_border_schedule_hu_ro():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = (start_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # Start in UTC for API
    period_end = (end_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # End in UTC for API

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": "10YHU-MAVIR----U",  # Outgoing country EIC code
        "in_Domain": "10YRO-TEL------P",   # Incoming country EIC code
        "periodStart": period_start,  # Start period in UTC
        "periodEnd": period_end,  # End period in UTC
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        # Extract the time interval and resolution
        start_time_utc = root.find('.//ns:TimeSeries/ns:Period/ns:timeInterval/ns:start', namespaces)
        resolution = root.find('.//ns:TimeSeries/ns:Period/ns:resolution', namespaces)

        if start_time_utc is None or resolution is None or resolution.text != "PT15M":
            print("No valid timeInterval or resolution detected.")
            return pd.DataFrame()

        start_time_utc = datetime.strptime(start_time_utc.text, '%Y-%m-%dT%H:%MZ')

        # Extract points
        positions = []
        quantities = []
        for point in root.findall('.//ns:Point', namespaces):
            position_tag = point.find('ns:position', namespaces)
            quantity_tag = point.find('ns:quantity', namespaces)

            if position_tag is not None and quantity_tag is not None:
                try:
                    positions.append(int(position_tag.text))
                    quantities.append(float(quantity_tag.text))
                except ValueError:
                    continue

        # Generate a full range of timestamps
        full_positions = list(range(1, 97))
        full_quantities = []

        # Fill missing positions by interpolating
        for i in full_positions:
            if i in positions:
                full_quantities.append(quantities[positions.index(i)])
            else:
                # Use the last known value if missing
                full_quantities.append(full_quantities[-1] if full_quantities else 0)

        # Generate timestamps in UTC
        timestamps_utc = [
            start_time_utc + timedelta(minutes=15 * (i - 1)) for i in full_positions
        ]

        # Convert timestamps to CET
        timestamps_cet = [
            pd.Timestamp(ts).tz_localize('UTC').tz_convert('Europe/Berlin')
            for ts in timestamps_utc
        ]

        # Create DataFrame
        df_schedule = pd.DataFrame({
            'Timestamp': timestamps_cet,
            'Scheduled Flow (MW)': full_quantities
        })

        # Filter only the current day's intervals in CET
        df_schedule = df_schedule[
            (df_schedule['Timestamp'] >= start_cet) &
            (df_schedule['Timestamp'] < end_cet)
        ].reset_index(drop=True)

        return df_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Scheduled Flow (MW)"])  # Return empty DataFrame on failure

def fetch_cross_border_schedule_quarterly_ro_hu():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Berlin')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Berlin')

    period_start = (start_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # Start in UTC for API
    period_end = (end_cet - timedelta(hours=2)).strftime('%Y%m%d%H%M')  # End in UTC for API

    url = "https://web-api.tp.entsoe.eu/api"

    params = {
        "securityToken": api_key_entsoe,  # Replace with your actual token
        "documentType": "A09",  # Document type for cross-border schedules
        "out_Domain": "10YRO-TEL------P",  # Outgoing country EIC code
        "in_Domain": "10YHU-MAVIR----U",   # Incoming country EIC code
        "periodStart": period_start,  # Start period in UTC
        "periodEnd": period_end,  # End period in UTC
        "contract_MarketAgreement.Type": "A05"
    }

    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.content)
        namespaces = {'ns': root.tag[root.tag.find("{"):root.tag.find("}")+1].strip("{}")}

        # Extract timeInterval
        start_time_utc = root.find('.//ns:TimeSeries/ns:Period/ns:timeInterval/ns:start', namespaces)
        resolution = root.find('.//ns:TimeSeries/ns:Period/ns:resolution', namespaces)

        if start_time_utc is None or resolution is None or resolution.text != "PT15M":
            print("No valid timeInterval or resolution detected.")
            return pd.DataFrame()

        start_time_utc = datetime.strptime(start_time_utc.text, '%Y-%m-%dT%H:%MZ')

        # Extract points
        positions = []
        quantities = []
        for point in root.findall('.//ns:Point', namespaces):
            position_tag = point.find('ns:position', namespaces)
            quantity_tag = point.find('ns:quantity', namespaces)

            if position_tag is None or quantity_tag is None:
                continue

            try:
                positions.append(int(position_tag.text))
                quantities.append(float(quantity_tag.text))
            except ValueError as e:
                print(f"Error converting position or quantity: {e}, skipping.")
                continue

        # Generate timestamps
        timestamps_utc = [
            start_time_utc + timedelta(minutes=15 * (pos - 1))
            for pos in positions
        ]

        # Convert to CET
        timestamps_cet = [
            pd.Timestamp(ts).tz_localize('UTC').tz_convert('Europe/Berlin')
            for ts in timestamps_utc
        ]

        # Create DataFrame
        df_schedule = pd.DataFrame({
            'Timestamp': timestamps_cet,
            'Scheduled Flow (MW)': quantities
        })

        # Filter only the desired CET range
        df_schedule = df_schedule[
            (df_schedule['Timestamp'] >= start_cet) &
            (df_schedule['Timestamp'] < end_cet)
        ]

        # Ensure the dataframe includes exactly 96 intervals
        full_index = pd.date_range(start=start_cet, end=end_cet - timedelta(minutes=15), freq='15T', tz='Europe/Berlin')
        df_schedule = df_schedule.set_index('Timestamp').reindex(full_index).reset_index()
        df_schedule.rename(columns={'index': 'Timestamp'}, inplace=True)

        # Forward-fill and backward-fill as needed
        df_schedule['Scheduled Flow (MW)'] = df_schedule['Scheduled Flow (MW)'].fillna(method='ffill').fillna(method='bfill')

        return df_schedule

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame(columns=["Timestamp", "Scheduled Flow (MW)"])  # Return empty DataFrame on failure


def combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled):
    # Rename schedule columns for clarity
    df_ro_hu_scheduled = df_ro_hu_scheduled.rename(columns={"Scheduled Flow (MW)": "RO → HU Scheduled Flow (MW)"})
    df_hu_ro_scheduled = df_hu_ro_scheduled.rename(columns={"Scheduled Flow (MW)": "HU → RO Scheduled Flow (MW)"})
    df_ro_hu_flow = df_ro_hu_flow.rename(columns={"Physical Flow (MW)": "RO → HU Physical Flow (MW)"})
    df_hu_ro_flow = df_hu_ro_flow.rename(columns={"Physical Flow (MW)": "HU → RO Physical Flow (MW)"})

    # Merge schedules into the cross-border physical flows dataframe
    df_combined = pd.merge(df_ro_hu_scheduled, df_hu_ro_scheduled, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_ro_hu_flow, on='Timestamp', how='outer')
    df_combined = pd.merge(df_combined, df_hu_ro_flow, on='Timestamp', how='outer')

    # Sort by Timestamp to ensure proper order
    df_combined = df_combined.sort_values(by='Timestamp').reset_index(drop=True)

    return df_combined

eic_Hungary = "10YHU-MAVIR----U" 

# IGCC Flows===============================================================================================================
def fetch_igcc_netting_flows():
	"""Fetch real-time activated balancing energy data from Transelectrica API for today in CET.
	Note: The API endpoint used seems to be for general activated balancing energy,
	not specifically IGCC netting. The field names 'imbalanceNettingImport' and
	'imbalanceNettingExport' are used, but might return 0 if not present in the response.
	"""

	# Define CET timezone
	cet_timezone = pytz.timezone("Europe/Berlin")

	# Get current time in CET and set midnight as start of the day
	cet_now = datetime.now(cet_timezone)
	cet_midnight = cet_now.replace(hour=0, minute=0, second=0, microsecond=0)

	# Convert CET midnight to UTC (Transelectrica API operates in UTC)
	utc_midnight = cet_midnight.astimezone(pytz.utc)
	utc_now = cet_now.astimezone(pytz.utc)

	# Set API time range: Fetch from CET midnight (converted to UTC) until now
	from_time = utc_midnight.strftime("%Y-%m-%dT%H:%M:%S.000Z")
	to_time = utc_now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

	# API Request (Transelectrica)
	# This URL fetches activatedBalancingEnergyOverview
	url = f"https://newmarkets.transelectrica.ro/usy-durom-publicreportg01/00121002500000000000000000000100/publicReport/estimatedPowerSystemImbalance?timeInterval.from={from_time}&timeInterval.to={to_time}&pageInfo.pageSize=3000"

	try:
		response = requests.get(url)
		# Check response status
		if response.status_code != 200:
			print(f"❌ Failed to fetch data for IGCC flows. Status code: {response.status_code}")
			return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])

		# Parse JSON response
		data = response.json()
		items = data.get("itemList", [])

		print(f"✅ Successfully fetched {len(items)} records for IGCC processing from API.")

		# Process and convert timestamps
		rows = []
		for item in items:
			try:
				# Convert timestamps from UTC to CET
				utc_from = datetime.fromisoformat(item['timeInterval']['from'].replace('Z', '+00:00'))
				cet_from = utc_from.astimezone(cet_timezone)

				# Use 'imbalanceNettingImport' and 'imbalanceNettingExport' fields if they exist
				netting_import = float(item.get("imbalanceNettingImport", 0) or 0)
				netting_export = float(item.get("imbalanceNettingExport", 0) or 0)

				rows.append([cet_from, netting_import, netting_export])
			except Exception as item_error:
				print(f"Error processing item for IGCC data: {item_error}") # Catch errors for individual items

		if not rows:
			print("⚠️ No valid IGCC data rows processed.")
			return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])

		df = pd.DataFrame(rows, columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
		# Ensure Timestamp column is datetime and remove timezone for consistency if needed later
		df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
		# Writing the Consumption Forecast to Excel
		df_igcc = df.copy()
		# Remove timezone information from the 'Timestamp' column
		df_igcc['Timestamp'] = df_igcc['Timestamp'].dt.tz_localize(None)
		# Save to Excel
		df_igcc.to_excel("./data_fetching/Entsoe/IGCC_Netting_Flows.xlsx", index=False)

		return df

	except requests.exceptions.RequestException as req_error:
		print(f"❌ Request failed for IGCC data: {req_error}")
		return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
	except Exception as json_error: # Catch JSON parsing errors or other issues
		print(f"❌ Error processing IGCC data response: {json_error}")
		return pd.DataFrame(columns=["Timestamp", "IGCC Import (MW)", "IGCC Export (MW)"])
    
#===================================================================Rendering and Testing=================================================================================

def render_test_entsoe_newapi_functions():
    # df_hu_ro_flow = fetch_physical_flows_ro_hu(eic_Hungary, eic_Romania)
    # df_ro_hu_flow = fetch_physical_flows_ro_hu(eic_Romania, eic_Hungary)

    # df_hu_ro_scheduled = fetch_cross_border_schedule_hu_ro()
    # df_ro_hu_scheduled = fetch_cross_border_schedule_quarterly_ro_hu()

    # df_ro_hu = combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled)
    # st.dataframe(df_ro_hu)

    #6. Storing the deviations in a dataframe========================================================================================================

    # Creating the deviations dataframe
    # Sample setup for timestamps (replace with actual range from your data)
    # timestamps = pd.date_range(start='2024-12-12 00:00:00', periods=96, freq='15T')

    # # Placeholder data for deviations from each source (replace these with actual calculations)
    # wind_deviation_actual = df_wind['Actual Production (MW)'] - df_wind['Notified Production (MW)']
    # wind_deviation_forecast_value = df_wind['Volue Forecast (MW)'] - df_wind['Notified Production (MW)']
    # wind_deviation_forecast_solcast = df_wind['Solcast Forecast (MW)'] - df_wind['Notified Production (MW)']

    # # Placeholder for other variables - replace with actual calculations
    # solar_deviation = [0] * len(timestamps)  # Example placeholder
    # hydro_deviation = [0] * len(timestamps)
    # consumption_deviation = [0] * len(timestamps)

    # # Cross-border deviations placeholders (replace with scheduled - actual flows)
    # hu_ro_deviation = [0] * len(timestamps)
    # ro_hu_deviation = [0] * len(timestamps)

    # # Construct the combined deviations DataFrame
    # df_deviations = pd.DataFrame({
    #     'Timestamp': df_wind['Timestamp'],
    #     'Wind_Deviation_Actual': wind_deviation_actual,
    #     'Wind_Deviation_Forecast_Value': wind_deviation_forecast_value,
    #     'Wind_Deviation_Forecast_Solcast': wind_deviation_forecast_solcast,
    #     'Solar_Deviation': solar_deviation,
    #     'Hydro_Deviation': hydro_deviation,
    #     'Consumption_Deviation': consumption_deviation,
    #     'HU_RO_Deviation': hu_ro_deviation,
    #     'RO_HU_Deviation': ro_hu_deviation
    # })

    # # Replace NaN or unavailable deviations with 0 or forecasts
    # df_deviations.fillna(0, inplace=True)

    # # Display the resulting combined deviations DataFrame
    # st.dataframe(df_deviations)

    # Wind Analysis================================================================================
    # df_wind_notified = fetch_process_wind_notified()
    # df_wind_actual = fetch_process_wind_actual_production()
    # st.dataframe(df_wind_actual)
    # df_wind_volue = preprocess_volue_forecast(fetch_volue_wind_data())
    # df_wind = combine_wind_production_data(df_wind_notified, df_wind_actual, df_wind_volue)

    # fetching_Cogealac_data_15min()
    # df_wind_solcast = predicting_wind_production_15min()
    # df_wind = add_solcast_forecast_to_wind_dataframe(df_wind, df_wind_solcast)
    # st.dataframe(df_wind)


    # # Replace 0 with NaN in 'Actual Production (MW)' to identify missing values
    # df_wind['Actual Production (MW)'] = df_wind['Actual Production (MW)'].replace(0, None)

    # # Add columns to indicate when forecasts should be used
    # df_wind['Volue Forecast (Filtered)'] = df_wind['Volue Forecast (MW)'].where(df_wind['Actual Production (MW)'].isna())
    # df_wind['Solcast Forecast (Filtered)'] = df_wind['Solcast Forecast (MW)'].where(df_wind['Actual Production (MW)'].isna())

    # df_wind = df_wind[df_wind["Notified Production (MW)"] > 0] 

    # # Create a long-format DataFrame for Plotly
    # df_wind_long = df_wind.melt(
    #     id_vars=['Timestamp'],
    #     value_vars=[
    #         'Actual Production (MW)',
    #         'Notified Production (MW)',
    #         'Volue Forecast (Filtered)',
    #         'Solcast Forecast (Filtered)'
    #     ],
    #     var_name='Type',
    #     value_name='Production (MW)'
    # )

    # # Remove rows where 'Production (MW)' is NaN
    # df_wind_long = df_wind_long[df_wind_long['Production (MW)'].notna()]


    # # Interactive dashboard header
    # st.header("Wind Production Monitoring")

    # # Plotting Actual vs Notified Wind Production with Forecasts
    # st.write("### Actual vs Notified Wind Production Over Time (With Forecasts)")
    # fig_wind_forecast = px.line(
    #     df_wind_long,
    #     x='Timestamp',
    #     y='Production (MW)',
    #     color='Type',
    #     line_dash='Type',
    #     labels={'Production (MW)': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual vs Notified Wind Production (With Forecasts)"
    # )

    # # Customize styles: Notified should always be solid
    # fig_wind_forecast.for_each_trace(lambda trace: trace.update(line_dash=None) if trace.name == 'Notified Production (MW)' else None)

    # # Show the plot
    # st.plotly_chart(fig_wind_forecast, use_container_width=True)

    # # Assuming df_wind is your DataFrame with the following columns:
    # # 'Timestamp', 'Notified Production (MW)', 'Actual Production (MW)', 'Volue Forecast (MW)', 'Solcast Forecast (MW)'

    # # Step 1: Compute deviations
    # df_wind['Deviation_Actual'] = df_wind['Actual Production (MW)'] - df_wind['Notified Production (MW)']
    # df_wind['Deviation_ValueForecast'] = df_wind['Volue Forecast (MW)'] - df_wind['Notified Production (MW)']
    # df_wind['Deviation_SolcastForecast'] = df_wind['Solcast Forecast (MW)'] - df_wind['Notified Production (MW)']

    # # Step 2: Combine Actual and Forecasted Deviations
    # df_wind['Deviation_Final'] = np.where(
    #     df_wind['Actual Production (MW)'].notna(),
    #     df_wind['Deviation_Actual'],
    #     np.nan
    # )

    # # For forecasting periods
    # forecast_mask = df_wind['Actual Production (MW)'].isna()
    # df_wind.loc[forecast_mask, 'Deviation_Final_ValueForecast'] = df_wind['Deviation_ValueForecast']
    # df_wind.loc[forecast_mask, 'Deviation_Final_SolcastForecast'] = df_wind['Deviation_SolcastForecast']

    # # Remove the last timestamp where 'Notified Production (MW)' is incomplete (e.g., NaN or 0)
    # df_wind_filtered = df_wind[df_wind['Notified Production (MW)'] > 0]

    # # Plotting the deviations
    # fig = px.line(df_wind_filtered, x='Timestamp', y='Deviation_Final',
    #               labels={'Deviation_Final': 'Deviation (MW)', 'Timestamp': 'Timestamp'},
    #               title="Actual and Forecasted Deviations from Notified Production")

    # # Add Value Forecast as dashed line
    # fig.add_scatter(x=df_wind_filtered['Timestamp'], 
    #                 y=df_wind_filtered['Deviation_Final_ValueForecast'], 
    #                 mode='lines', 
    #                 line=dict(dash='dash', color='red'), 
    #                 name='Deviation - Value Forecast')

    # # Add Solcast Forecast as dashed line
    # fig.add_scatter(x=df_wind_filtered['Timestamp'], 
    #                 y=df_wind_filtered['Deviation_Final_SolcastForecast'], 
    #                 mode='lines', 
    #                 line=dict(dash='dash', color='orange'), 
    #                 name='Deviation - Solcast Forecast')

    # fig.update_layout(
    #     xaxis_title='Timestamp',
    #     yaxis_title='Deviation (MW)',
    #     legend_title='Type'
    # )

    # # Display the chart in Streamlit
    # st.plotly_chart(fig, use_container_width=True)



    # Solar Production Analysis===================================================================
    # df_solar_notified = fetch_process_solar_notified()
    # df_solar_actual = fetch_process_solar_actual_production()
    # df_solar_volue = preprocess_volue_forecast(fetch_volue_solar_data())
    # df_solar = combine_solar_production_data(df_solar_notified, df_solar_actual, df_solar_volue)

    # st.dataframe(df_solar)

    # import pandas as pd
    # import plotly.express as px
    # import streamlit as st
    # import numpy as np


    # # Step 1: Identify the last interval with actual production > 0
    # last_actual_index = df_solar[df_solar['Actual Production (MW)'] > 0].index.max()

    # # Step 2: Compute Deviations
    # df_solar['Deviation_Actual'] = df_solar['Actual Production (MW)'] - df_solar['Notified Production (MW)']
    # df_solar['Deviation_Forecast'] = df_solar['Volue Forecast (MW)'] - df_solar['Notified Production (MW)']

    # # Step 3: Split the data for solid and dashed lines
    # df_solar['Deviation_Combined'] = np.where(
    #     df_solar.index <= last_actual_index,
    #     df_solar['Deviation_Actual'],
    #     np.nan
    # )
    # df_solar['Deviation_Forecast_Line'] = np.where(
    #     df_solar.index > last_actual_index,
    #     df_solar['Deviation_Forecast'],
    #     np.nan
    # )

    # # Step 4: Visualization for Actual vs Notified Solar Production
    # st.title("Solar Production Monitoring")

    # # Plot 1: Actual vs Notified Solar Production Over Time
    # st.subheader("Actual vs Notified Solar Production Over Time")
    # fig_actual_vs_notified = px.line(
    #     df_solar, 
    #     x='Timestamp', 
    #     y=['Notified Production (MW)', 'Actual Production (MW)', 'Volue Forecast (MW)'],
    #     labels={'value': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual vs Notified Solar Production (With Forecast)"
    # )
    # # Update line styles
    # fig_actual_vs_notified.update_traces(selector=dict(name='Notified Production (MW)'),
    #                                      line=dict(color='blue', dash='solid'))
    # fig_actual_vs_notified.update_traces(selector=dict(name='Actual Production (MW)'),
    #                                      line=dict(color='skyblue', dash='solid'))
    # fig_actual_vs_notified.update_traces(selector=dict(name='Volue Forecast (MW)'),
    #                                      line=dict(color='orange', dash='dash'))

    # st.plotly_chart(fig_actual_vs_notified, use_container_width=True)

    # # Plot 2: Actual and Forecasted Deviations
    # st.subheader("Actual and Forecasted Deviations from Notified Solar Production")
    # fig_deviation = px.line(
    #     df_solar, 
    #     x='Timestamp', 
    #     y=['Deviation_Combined', 'Deviation_Forecast_Line'],
    #     labels={'value': 'Deviation (MW)', 'Timestamp': 'Timestamp'},
    #     title="Actual and Forecasted Deviations from Notified Solar Production"
    # )
    # # Update line styles
    # fig_deviation.update_traces(selector=dict(name='Deviation_Combined'),
    #                             line=dict(color='skyblue', dash='solid'),
    #                             name="Deviation - Actual Production")
    # fig_deviation.update_traces(selector=dict(name='Deviation_Forecast_Line'),
    #                             line=dict(color='orange', dash='dash'),
    #                             name="Deviation - Forecast")

    # st.plotly_chart(fig_deviation, use_container_width=True)


    # Step 6: Add Deviations to Centralized DataFrame
    # df_deviations['Solar_Deviation'] = df_solar['Solar_Deviation_Final']

    # Hydro Production Analysis=========================================================================================

    # df_hydro_reservoir_actual = fetch_process_hydro_water_reservoir_actual_production()
    # df_hydro_river_actual = fetch_process_hydro_river_actual_production()
    # df_hydro_volue = fetch_volue_hydro_data()
    # df_hydro = align_and_combine_hydro_data(df_hydro_reservoir_actual, df_hydro_river_actual, df_hydro_volue)

    # st.dataframe(df_hydro)

    # # Combine Actual Hydro Production (Hydro Reservoir + Hydro River)
    # df_hydro['Hydro_Actual'] = df_hydro['Hydro Reservoir Actual (MW)'] + df_hydro['Hydro River Actual (MW)']

    # # Identify the last interval with actual production > 0
    # last_actual_index = df_hydro[df_hydro['Hydro_Actual'] > 0].index.max()

    # # Replace Hydro Actual after the last valid production with None
    # df_hydro.loc[last_actual_index + 1:, 'Hydro_Actual'] = None

    # # Melt the DataFrame to long format for Plotly Express
    # df_hydro_long = df_hydro.melt(id_vars=['Timestamp'], 
    #                               value_vars=['Hydro_Actual', 'Volue Forecast (MW)'],
    #                               var_name='Type', value_name='Production')

    # # Create line dash mapping
    # line_dash_map = {
    #     'Hydro_Actual': 'solid',
    #     'Volue Forecast (MW)': 'dash'
    # }

    # # Visualization: Hydro Actual vs Forecast
    # st.header("Hydro Production Monitoring")
    # st.subheader("Actual vs Forecasted Hydro Production")

    # fig_hydro_actual_forecast = px.line(
    #     df_hydro_long, 
    #     x='Timestamp', 
    #     y='Production', 
    #     color='Type', 
    #     line_dash='Type',
    #     line_dash_map=line_dash_map,
    #     title="Actual vs Forecasted Hydro Production",
    #     labels={'Production': 'Production (MW)', 'Timestamp': 'Timestamp'},
    #     color_discrete_map={
    #         'Hydro_Actual': 'blue',
    #         'Volue Forecast (MW)': 'orange'
    #     }
    # )

    # st.plotly_chart(fig_hydro_actual_forecast, use_container_width=True)

    # Consumption Analysis=========================================================================================

    # df_consumption_forecast = fetch_consumption_forecast()
    # df_consumption_actual = fetch_actual_consumption()
    # df_consumption = combine_consumption_data(df_consumption_forecast, df_consumption_actual)
    # st.dataframe(df_consumption)

    # import plotly.express as px
    # import streamlit as st

    # # Load the existing DataFrame
    # # Replace this with the actual dataframe you already have

    # # Drop rows with None in 'Actual Consumption (MW)' for clean plotting
    # df_actual = df_consumption.dropna(subset=['Actual Consumption (MW)'])

    # # Plotly Graph: Actual vs Forecasted Consumption
    # fig = px.line()

    # # Add Actual Consumption line
    # fig.add_scatter(
    #     x=df_actual['Timestamp'],
    #     y=df_actual['Actual Consumption (MW)'],
    #     mode='lines',
    #     name='Actual Consumption (MW)',
    #     line=dict(color='#1f77b4')  # Blue line
    # )

    # # Add Forecasted Consumption line
    # fig.add_scatter(
    #     x=df_consumption['Timestamp'],
    #     y=df_consumption['Consumption Forecast (MW)'],
    #     mode='lines',
    #     name='Consumption Forecast (MW)',
    #     line=dict(color='#ff7f0e')  # Orange line
    # )

    # # Update layout for clarity
    # fig.update_layout(
    #     title="Actual vs Forecasted Consumption Over Time",
    #     xaxis_title="Timestamp",
    #     yaxis_title="Consumption (MW)",
    #     legend_title="Type",
    #     template="plotly_dark",
    #     hovermode="x unified"
    # )

    # # Display in Streamlit
    # st.header("Consumption Monitoring")
    # st.write("### Actual vs Forecasted Consumption Over Time")
    # st.plotly_chart(fig, use_container_width=True)

    # Cross Border Analysis=========================================================================================

    # RO_BG
    df_physical_flow_bg_ro = fetch_physical_flows_bulgaria_to_romania()
    df_physical_flow_ro_bg = fetch_physical_flows_romania_to_bulgaria()
    df_physical_flows_ro_bg = concatenate_cross_border_flows(df_physical_flow_bg_ro, df_physical_flow_ro_bg)
    df_scheduled_flow_ro_bg = fetch_cross_border_schedule(eic_Romania, eic_Bulgaria)
    df_scheduled_flow_bg_ro = fetch_cross_border_schedule(eic_Bulgaria, eic_Romania)
    df_ro_bg = combine_physical_and_scheduled_flows_ro_bg(df_physical_flows_ro_bg, df_scheduled_flow_bg_ro, df_scheduled_flow_ro_bg)


    def calculate_excedent_deficit_ro_bg(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → BG Scheduled Flow (MW)'] - df['BG → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → BG Flow (MW)'] - df['BG → RO Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    # Apply the logic to your dataframe
    df_ro_bg = calculate_excedent_deficit_ro_bg(df_ro_bg, "Excedent_RO_BG (MW)", "Deficit_RO_BG (MW)")


    st.dataframe(df_ro_bg)

    # RO_RS
    def calculate_excedent_deficit_ro_rs(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → RS Scheduled Flow (MW)'] - df['RS → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → RS Physical Flow (MW)'] - df['RS → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df
    df_physical_flow_ro_rs = fetch_physical_flows(eic_Romania, eic_Serbia)
    df_physical_flow_rs_ro = fetch_physical_flows(eic_Serbia, eic_Romania)
    df_crossborder_flow_ro_rs = fetch_cross_border_schedule(eic_Romania, eic_Serbia)
    df_crossborder_flow_rs_ro = fetch_cross_border_schedule(eic_Serbia, eic_Romania)
    df_ro_rs = combine_physical_and_scheduled_flows_ro_rs(df_physical_flow_rs_ro, df_physical_flow_ro_rs, df_crossborder_flow_rs_ro, df_crossborder_flow_ro_rs)
    # Apply the logic to your dataframe
    df_ro_rs = calculate_excedent_deficit_ro_rs(df_ro_rs, "Excedent_RO_RS (MW)", "Deficit_RO_RS (MW)")
    st.dataframe(df_ro_rs)

    # RO_HU
    def calculate_excedent_deficit_ro_hu(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → HU Scheduled Flow (MW)'] - df['HU → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → HU Physical Flow (MW)'] - df['HU → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_hu_ro_flow = fetch_physical_flows_ro_hu(eic_Hungary, eic_Romania)
    df_ro_hu_flow = fetch_physical_flows_ro_hu(eic_Romania, eic_Hungary)

    df_hu_ro_scheduled = fetch_cross_border_schedule_hu_ro()
    df_ro_hu_scheduled = fetch_cross_border_schedule_quarterly_ro_hu()

    df_ro_hu = combine_physical_and_scheduled_flows_ro_hu(df_hu_ro_flow, df_ro_hu_flow, df_hu_ro_scheduled, df_ro_hu_scheduled)
    # Apply the logic to your dataframe
    df_ro_hu = calculate_excedent_deficit_ro_hu(df_ro_hu, "Excedent_RO_HU (MW)", "Deficit_RO_HU (MW)")
    st.dataframe(df_ro_hu)

    # RO_MD
    def calculate_excedent_deficit_ro_md(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → MD Scheduled Flow (MW)'] - df['MD → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → MD Physical Flow (MW)'] - df['MD → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_physical_flow_ro_md = fetch_physical_flows(eic_Romania, eic_Moldova)
    df_physical_flow_md_ro = fetch_physical_flows(eic_Moldova, eic_Romania)
    df_crossborder_flow_ro_md = fetch_cross_border_schedule(eic_Romania, eic_Moldova)
    df_crossborder_flow_md_ro = fetch_cross_border_schedule(eic_Moldova, eic_Romania)

    df_ro_md = combine_physical_and_scheduled_flows_ro_md(df_physical_flow_md_ro, df_physical_flow_ro_md, df_crossborder_flow_md_ro, df_crossborder_flow_ro_md)
    # # Apply the logic to your dataframe
    df_ro_md = calculate_excedent_deficit_ro_md(df_ro_md, "Excedent_RO_MD (MW)", "Deficit_RO_MD (MW)")
    st.dataframe(df_ro_md)

    # RO_UA
    def calculate_excedent_deficit_ro_ua(df, excedent, deficit):
        df['Net Scheduled Flow (MW)'] = df['RO → UA Scheduled Flow (MW)'] - df['UA → RO Scheduled Flow (MW)']
        df['Net Physical Flow (MW)'] = df['RO → UA Physical Flow (MW)'] - df['UA → RO Physical Flow (MW)']
        
        # Initialize deficit and excedent
        df[excedent] = 0
        df[deficit] = 0

        # Excedent: Net Physical Flow < Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] < df['Net Scheduled Flow (MW)'], excedent] = \
            df['Net Scheduled Flow (MW)'] - df['Net Physical Flow (MW)']

        # Deficit: Net Physical Flow > Net Scheduled Flow
        df.loc[df['Net Physical Flow (MW)'] > df['Net Scheduled Flow (MW)'], deficit] = \
            df['Net Physical Flow (MW)'] - df['Net Scheduled Flow (MW)']

        return df

    df_physical_flow_ro_ua = fetch_physical_flows(eic_Romania, eic_Ukraine)
    df_physical_flow_ua_ro = fetch_physical_flows(eic_Ukraine, eic_Romania)
    df_crossborder_flow_ro_ua = fetch_cross_border_schedule(eic_Romania, eic_Ukraine)
    df_crossborder_flow_ua_ro = fetch_cross_border_schedule(eic_Ukraine, eic_Romania)
    df_ro_ua = combine_physical_and_scheduled_flows_ro_ua(df_physical_flow_ua_ro, df_physical_flow_ro_ua, df_crossborder_flow_ua_ro, df_crossborder_flow_ro_ua)

    # Apply the logic to your dataframe
    df_ro_ua = calculate_excedent_deficit_ro_ua(df_ro_ua, "Excedent_RO_UA (MW)", "Deficit_RO_UA (MW)")
    st.dataframe(df_ro_ua)

    # Creating the Deficit and Excedent dataframe to store all deficits and excedents
    def create_aligned_deficit_excedent_dataframe(df_ro_bg, df_ro_rs, df_ro_hu, df_ro_md, df_ro_ua):
        # Select the required columns and rename for clarity
        ro_bg = df_ro_bg[['Timestamp', 'Excedent_RO_BG (MW)', 'Deficit_RO_BG (MW)']].copy()
        ro_rs = df_ro_rs[['Timestamp', 'Excedent_RO_RS (MW)', 'Deficit_RO_RS (MW)']].copy()
        ro_hu = df_ro_hu[['Timestamp', 'Excedent_RO_HU (MW)', 'Deficit_RO_HU (MW)']].copy()
        ro_md = df_ro_md[['Timestamp', 'Excedent_RO_MD (MW)', 'Deficit_RO_MD (MW)']].copy()
        ro_ua = df_ro_ua[['Timestamp', 'Excedent_RO_UA (MW)', 'Deficit_RO_UA (MW)']].copy()

        # Merge all dataframes on Timestamp to align them
        aligned_df = pd.merge(ro_bg, ro_rs, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_hu, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_md, on='Timestamp', how='outer')
        aligned_df = pd.merge(aligned_df, ro_ua, on='Timestamp', how='outer')
        # Fill NaN values with 0, assuming missing values mean no deficit/excedent
        aligned_df.fillna(0, inplace=True)

        # Calculate Total Excedent and Total Deficit
        aligned_df['Total Excedent (MW)'] = (
            aligned_df['Excedent_RO_BG (MW)'] +
            aligned_df['Excedent_RO_RS (MW)'] +
            aligned_df['Excedent_RO_HU (MW)'] +
            aligned_df['Excedent_RO_MD (MW)'] +
            aligned_df['Excedent_RO_UA (MW)']
        )
        aligned_df['Total Deficit (MW)'] = (
            aligned_df['Deficit_RO_BG (MW)'] +
            aligned_df['Deficit_RO_RS (MW)'] +
            aligned_df['Deficit_RO_HU (MW)'] +
            aligned_df['Deficit_RO_MD (MW)'] +
            aligned_df['Deficit_RO_UA (MW)']
        )

        # Calculate the System Direction
        aligned_df['System Direction (MW)'] = (
        aligned_df['Total Excedent (MW)'] - aligned_df['Total Deficit (MW)']
        )
        
        # Sort by Timestamp for a cleaner view
        aligned_df.sort_values(by='Timestamp', inplace=True)

        # Writing the Border Flows to Excel
        aligned_df_excel = aligned_df.copy()
        # Remove timezone information from the 'Timestamp' column
        aligned_df_excel['Timestamp'] = aligned_df_excel['Timestamp'].dt.tz_localize(None)
        # Save to Excel
        aligned_df_excel.to_excel("./data_fetching/Entsoe/Border_Flows.xlsx", index=False)

        return aligned_df

    # Example usage:
    df_deficit_excedent_aligned = create_aligned_deficit_excedent_dataframe(df_ro_bg, df_ro_rs, df_ro_hu, df_ro_md, df_ro_ua)

    st.dataframe(df_deficit_excedent_aligned)