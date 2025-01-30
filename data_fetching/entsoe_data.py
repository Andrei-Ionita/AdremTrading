import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from entsoe import EntsoePandasClient
import os
from dotenv import load_dotenv
import wapi
import requests
import xml.etree.ElementTree as ET

# Loading the ENTSO-E API key
load_dotenv()  # Load variables from .env file
api_key_entsoe = os.getenv("api_key_entsoe")
client = EntsoePandasClient(api_key=api_key_entsoe)

# Loading the Volue API key
client_id = os.getenv("volue_client_id")
client_secret = os.getenv("volue_client_secret")

# Function to get issue date - we'll use it internally for date fetching
def get_issue_date():
    issue_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return issue_date

# Volue API setup================================================================
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

#========================================================================Fetching Imbalance Data===================================================================================================
# Function to fetch imbalance prices
def imbalance_prices(start, end):
    country_code = 'RO'
    return client.query_imbalance_prices(country_code, start=start, end=end, psr_type=None)

# Function to fetch imbalance volumes
def imbalance_volumes(start, end):
    country_code = 'RO'
    return client.query_imbalance_volumes(country_code, start=start, end=end, psr_type=None)

# Function to fetch and merge imbalance data for today and tomorrow
def fetch_intraday_imbalance_data():
    # Setting up the start and end dates (today and tomorrow)
    today = get_issue_date()
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Fetching imbalance prices and volumes
    df_imbalance_prices = imbalance_prices(start_cet, end_cet)
    df_imbalance_volumes = imbalance_volumes(start_cet, end_cet)

    # Merging the DataFrames on their indices
    df_imbalance = pd.merge(df_imbalance_prices, df_imbalance_volumes, left_index=True, right_index=True, how='inner')
    df_imbalance = df_imbalance.rename(columns={'Long': 'Excedent Price', 'Short': 'Deficit Price'})

    # Returning the merged DataFrame
    return df_imbalance

#==========================================================================Fetching Fundamentals Data=========================================================================================================
# 1.Wind Production data
def wind_solar_generation():
    # Setting up the start and end dates (today and tomorrow)
    today = get_issue_date()
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Set the country code for Romania
    country_code = 'RO'  # ISO-3166 alpha-2 code for Romania
    
    # Fetch Wind and Solar Generation
    wind_solar_generation = client.query_intraday_wind_and_solar_forecast(country_code, start=start_cet, end=end_cet, psr_type=None)
    wind_solar_generation.reset_index(inplace=True)
    wind_solar_generation.rename(columns = {"index": "Timestamp"}, inplace=True)
    return wind_solar_generation

def actual_generation_source():
    # Setting up the start and end dates (today and tomorrow)
    today = get_issue_date()
    start_cet = pd.Timestamp(today.strftime('%Y%m%d') + '0000', tz='Europe/Budapest')
    end_cet = pd.Timestamp((today + timedelta(days=1)).strftime('%Y%m%d') + '0000', tz='Europe/Budapest')

    # Set the country code for Romania
    country_code = 'RO'  # ISO-3166 alpha-2 code for Romania

    # Fetch imbalance prices
    actual_generation_source = client.query_generation(country_code, start=start_cet, end=end_cet, psr_type=None, include_eic=False)

    # Display or analyze the fetched data
    return actual_generation_source

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

#================Testing the function============================================
# if st.button("Fetch data"):
#     st.dataframe(fetch_intraday_imbalance_data())
#     # Fetch notified and actual wind production and create the combined dataframe
#     df_notified_wind_solar = wind_solar_generation()
#     df_actual_generation = actual_generation_source()
    
#     # Reset the index and rename it to "Timestamp"
#     df_actual_generation.reset_index(inplace=True)
#     df_actual_generation.rename(columns={"index": "Timestamp"}, inplace=True)

#     # Filter only wind production data
#     df_actual_wind = df_actual_generation[["Timestamp", "Wind Onshore"]]

#     # Set "Timestamp" as the index again to keep it aligned with other operations
#     df_actual_wind.set_index("Timestamp", inplace=True)

#     df_notified_wind = df_notified_wind_solar.drop("Solar", axis=1)
#     st.dataframe(df_actual_wind)
#     st.dataframe(df_notified_wind)
#     # Merge the notified and actual wind production into a single DataFrame on timestamp using a left join
#     df_combined_wind = pd.merge(df_notified_wind, df_actual_wind, on="Timestamp", how="left", suffixes=("_Notified", "_Actual"))

#     # Sort by Timestamp to maintain chronological order
#     df_combined_wind = df_combined_wind.sort_values(by="Timestamp")

#     # Display the combined DataFrame for validation
#     st.dataframe(df_combined_wind)

#     df_combined_wind = df_combined_wind.rename(columns={"Wind Onshore_Actual": "Wind_Actual", "Wind Onshore_Notified": "Wind_Notified"})
#     df_combined_wind['Deviation'] = df_combined_wind['Wind_Actual'] - df_combined_wind['Wind_Notified']
#     df_combined_wind = df_combined_wind.sort_values(by="Timestamp")

#     st.dataframe(fetch_volue_wind_data())

