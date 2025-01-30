import streamlit as st
from streamlit_gsheets import GSheetsConnection

url = "https://docs.google.com/spreadsheets/d/1zTx9eJV67sNHxUEGvIQiMOR7LarLZWqmsVi3hfZz1gw/edit?gid=0#gid=0"

conn = st.connection("gsheets", type=GSheetsConnection)

# Specify the worksheet name or index here
data = conn.read(spreadsheet=url)  # Replace "Sheet1" with your worksheet name

# Write the data to a specific sheet
conn.write("Andrei", start_cell="A1")

st.dataframe(data)
