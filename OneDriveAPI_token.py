import msal
import streamlit as st
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Your app's client ID, tenant ID, and client secret
client_id = os.getenv('client_id') or st.secrets["oauth"]["client_id"]
tenant_id = os.getenv('tenant_id') or st.secrets["oauth"]["tenant_id"]
client_secret = os.getenv('client_secret') or st.secrets["oauth"]["client_secret"]

# The authority URL and scope for Microsoft Graph API
# authority = f"https://login.microsoftonline.com/{tenant_id}"
# scope = ["https://graph.microsoft.com/.default"]

# # Create a client instance of MSAL
# app = msal.ConfidentialClientApplication(
#     client_id, authority=authority, client_credential=client_secret
# )

# # Acquire token
# result = app.acquire_token_for_client(scopes=scope)

# if "access_token" in result:
#     print("Access token: ", result["access_token"])
# else:
#     print("Error: ", result.get("error"), result.get("error_description"))

# The authority URL and scope for Microsoft Graph API
authority = f"https://login.microsoftonline.com/{tenant_id}"
scope = ["https://graph.microsoft.com/.default"]

# Create a client instance of MSAL
app = msal.ConfidentialClientApplication(
    client_id, authority=authority, client_credential=client_secret
)

def get_token():
    """Gets a new access token using MSAL if needed."""
    if token_is_expired():
        # Acquire a new access token
        result = app.acquire_token_for_client(scopes=scope)
        if "access_token" in result:
            # Store the new token and expiration time in session state
            access_token = result["access_token"]
            expires_in = result["expires_in"]  # Expiration time in seconds
            update_session_state(access_token, expires_in)
            return access_token
        else:
            raise Exception(f"Error: {result.get('error')}, {result.get('error_description')}")
    else:
        # Return the current access token
        return st.session_state["access_token"]

def update_session_state(access_token, expiry_time):
    """Updates the session state with the new access token and expiration time."""
    expiration_timestamp = datetime.now() + timedelta(seconds=expiry_time)
    
    st.session_state["access_token"] = access_token
    st.session_state["expiration_time"] = expiration_timestamp.isoformat()

def token_is_expired():
    """Checks whether the access token has expired or is about to expire (buffered by 2 minutes)."""
    expiration_time_str = st.session_state.get("expiration_time")
    if expiration_time_str:
        expiration_time = datetime.fromisoformat(expiration_time_str)
        return datetime.now() >= (expiration_time - timedelta(minutes=2))
    else:
        return True  # If there's no expiration time, consider the token expired

# Example of using the token
def upload_file_to_onedrive():
    access_token = get_token()  # Automatically refreshes the token if needed
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream"
    }
    return st.session_state["access_token"], st.session_state["expiration_time"]

    # Your upload logic here...

# get_token()
# print(upload_file_to_onedrive())

import requests

# Access token from your previous script
access_token = get_token()
st.write(access_token)
# User email or ID (replace with actual user email or user ID)
user_principal_name = 'andrei.ionita@adrem.ro'

# Endpoint to list root contents
list_url = f"https://graph.microsoft.com/v1.0/users/{user_principal_name}/drive/root/children"

# Set headers
headers = {
    "Authorization": f"Bearer {access_token}"
}

# Make the GET request to list the items in the root directory
response = requests.get(list_url, headers=headers)

if response.status_code == 200:
    items = response.json().get('value', [])
    for item in items:
        print(f"Name: {item['name']}, Type: {item['folder'] if 'folder' in item else 'File'}")
else:
    print(f"Failed to list root contents. Status code: {response.status_code}")
    print(response.json())
