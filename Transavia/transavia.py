import streamlit as st
import streamlit.components.v1 as stc
import pandas as pd
import numpy as np
import base64
import xgboost as xgb
import joblib
import xlsxwriter
import os
import time
import zipfile
from datetime import datetime
import gdown
import requests
from openpyxl import load_workbook
import pytz
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


session_start_time = time.time()

# Creating the holidays dataframe
# Creating the dictionary of holidays
New_year_and_day_after = pd.DataFrame({"holiday": "Anul Nou & A doua zi",
														"ds": pd.to_datetime(["2017-01-01", "2017-01-02", "2016-01-01", "2016-01-02", "2015-01-01", "2015-01-02", "2014-01-01", "2014-01-02", "2019-01-01",
																									"2019-01-02", "2018-01-01", "2018-01-02", "2020-01-01", "2020-01-02", "2021-01-01", "2021-01-02",
																									"2022-01-01", "2022-01-02", "2023-01-01", "2023-01-02","2024-01-01", "2024-01-02"]),
														"lower_window": -1,
														"upper_window": 1})	

National_holiday = pd.DataFrame({"holiday": "Ziua Nationala",
																 "ds": pd.to_datetime(["2016-12-01", "2015-12-01", "2014-12-01", "2018-12-01", "2019-12-01", "2020-12-01", "2021-12-01", "2022-12-01", "2023-12-01", "2024-12-01"]),
																 "lower_window": 0,
																 "upper_window": 1})
Ziua_Principatelor = pd.DataFrame({"holiday": "Ziua Principatelor",
																 "ds": pd.to_datetime(["2017-01-24", "2016-01-24", "2018-01-24", "2019-01-24", "2020-01-24", "2021-01-24", "2022-01-24", "2023-01-24", "2024-01-24"]),
																 "lower_window": 0,
																 "upper_window": 1})
Christmas = pd.DataFrame({"holiday": "Craciunul",
													"ds": pd.to_datetime(["2017-12-25", "2017-12-26", "2016-12-25", "2016-12-26", "2015-12-25", "2015-12-26", "2014-12-25", "2014-12-26", "2018-12-25", "2018-12-26", "2019-12-25", "2019-12-26", "2020-12-25", "2020-12-26", "2021-12-25", "2021-12-26",
																								"2022-12-25", "2022-12-26", "2023-12-25", "2023-12-26", "2024-12-25", "2024-12-26"]),
													"lower_window": -1,
													"upper_window": 1})
St_Andrew = pd.DataFrame({"holiday": "Sfantul Andrei",
													"ds": pd.to_datetime(["2017-11-30", "2016-11-30", "2015-11-30", "2014-11-30", "2018-11-30", "2019-11-30", "2020-11-30", "2021-11-30", "2022-11-30",
																								"2023-11-30", "2024-11-30"]),
													"lower_window": -1,
													"upper_window": 0})
Adormirea_Maicii_Domnului = pd.DataFrame({"holiday": "Adormirea Maicii Domnului",
																					"ds": pd.to_datetime(["2017-08-15", "2016-08-15", "2015-08-15", "2014-08-15", "2018-08-15", "2019-08-15", "2020-08-15", "2021-08-15","2022-08-15", "2024-08-15"])})
Rusalii = pd.DataFrame({"holiday": "Rusalii",
												"ds": pd.to_datetime(["2017-06-04", "2017-06-05", "2016-06-19", "2016-06-20", "2015-05-31", "2015-06-01", "2014-06-08", "2014-06-09", "2018-05-27", "2018-05-28", "2019-06-16", "2019-06-17", "2020-06-07", "2020-06-08", "2021-06-20", "2021-06-21",
																							"2022-06-12", "2022-06-13", "2023-06-04", "2023-06-05", "2024-06-24"])})
Ziua_Copilului = pd.DataFrame({"holiday": "Ziua Copilului",
															"ds": pd.to_datetime(["2017-06-01", "2018-06-01", "2019-06-01", "2020-06-01", "2021-06-01", "2022-06-01", "2023-06-01", "2024-06-01"])})
Ziua_Muncii = pd.DataFrame({"holiday": "Ziua Muncii",
														"ds": pd.to_datetime(["2017-05-01", "2016-05-01", "2015-05-01", "2014-05-01", "2018-05-01", "2019-05-01", "2020-05-01", "2021-05-01", "2022-05-01", "2023-05-01",
															"2024-05-01"])})
Pastele = pd.DataFrame({"holiday": "Pastele",
												"ds": pd.to_datetime(["2017-04-16", "2017-04-17", "2016-05-01", "2016-05-02", "2015-04-12", "2015-04-13", "2014-04-20", "2014-04-21", "2018-04-08", "2018-04-09", "2019-04-28", "2019-04-29", "2020-04-19", "2020-04-20", "2021-05-02", "2021-05-03",
																							"2022-04-24", "2022-04-25", "2023-04-16", "2023-04-17", "2024-05-06"]),
												"lower_window": -1,
												"upper_window": 1})
Vinerea_Mare = pd.DataFrame({"holiday": "Vinerea Mare",
														 "ds": pd.to_datetime(["2020-04-17", "2019-04-26", "2018-04-06", "2021-04-30", "2022-04-30", "2023-04-30", "2024-05-03"])})
Ziua_Unirii = pd.DataFrame({"holiday": "Ziua Unirii",
														"ds": pd.to_datetime(["2015-01-24", "2020-01-24", "2019-01-24", "2021-01-24", "2022-01-24", "2023-01-24", "2024-01-24"])})
Public_Holiday = pd.DataFrame({"holiday": "Public Holiday",
															"ds": pd.to_datetime(["2019-04-30"])})
holidays = pd.concat((New_year_and_day_after, National_holiday, Christmas, St_Andrew, Ziua_Principatelor, Adormirea_Maicii_Domnului, Rusalii, Ziua_Copilului, Ziua_Muncii,
											Pastele, Vinerea_Mare, Ziua_Unirii, Public_Holiday))

#=============================================================================Fetching the data for Transavia locations========================================================================
solcast_api_key = os.getenv("solcast_api_key")
# output_path = "./Transavia/data/Bocsa.csv"
# print("API Key:", solcast_api_key)  # Remove after debugging
if 'solcast_api_key' not in st.session_state:
	st.session_state['solcast_api_key'] = solcast_api_key = "pJFKpjXVuATTf72TB4hRc6lfu5W3Ww4_"

# Defining the fetching data function
def fetch_data(lat, lon, api_key, output_path):
	# Fetch data from the API
	api_url = "https://api.solcast.com.au/data/forecast/radiation_and_weather?latitude={}&longitude={}&hours=168&output_parameters=air_temp,cloud_opacity,ghi&period=PT60M&format=csv&api_key={}".format(lat, lon, solcast_api_key)
	response = requests.get(api_url)
	print("Fetching data...")
	if response.status_code == 200:
		# Write the content to a CSV file
		with open(output_path, 'wb') as file:
			file.write(response.content)
	else:
		print(response.text)  # Add this line to see the error message returned by the API
		raise Exception(f"Failed to fetch data: Status code {response.status_code}")

# ============================Crating the Input_production file==========
def creating_input_production_file(path):
	santimbru_data = pd.read_csv(f"{path}/Santimbru.csv")
	input_production = pd.read_excel("./Transavia/Production/Input_production.xlsx")
	input_production = input_production.copy()

	# Convert 'period_end' in santimbru to datetime
	santimbru_data['period_end'] = pd.to_datetime(santimbru_data['period_end'], errors='coerce')
	# Shift the 'period_end' column by 2 hours
	santimbru_data['period_end'] = santimbru_data['period_end'] + pd.Timedelta(hours=-2)
	# Then, convert the datetime to EET (taking into account DST if applicable)
	santimbru_data['period_end_EET'] = santimbru_data['period_end'].dt.tz_convert('Europe/Bucharest')
	# Extract just the date part in the desired format (as strings)
	santimbru_dates = santimbru_data['period_end_EET'].dt.strftime('%Y-%m-%d')

	# Write the dates from santimbru_dates to input_production.Data
	input_production['Data'] = santimbru_dates.values
	# Fill NaNs in the 'Data' column with next valid observation
	input_production['Data'].fillna(method='bfill', inplace=True)

	# Completing the Interval column
	santimbru_intervals = santimbru_data["period_end_EET"].dt.hour
	input_production["Interval"] = santimbru_intervals
	# Replace NaNs in the 'Interval' column with 0
	input_production['Interval'].fillna(0, inplace=True)

	# Completing the Radiatie column
	santimbru_radiatie = santimbru_data["ghi"]
	input_production["Radiatie"] = santimbru_radiatie

	# Completing the Temperatura column
	santimbru_temperatura = santimbru_data["air_temp"]
	input_production["Temperatura"] = santimbru_temperatura

	# Completing the Nori column
	santimbru_nori = santimbru_data["cloud_opacity"]
	input_production["Nori"] = santimbru_nori

	# Completing the Centrala column
	input_production["Centrala"] = "Abator_Oiejdea"

	# Copying the data for FNC PVPP
	copy_df = input_production.copy()
	copy_df['Centrala'] = "FNC"

	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	# Copying the data for F4 PVPP
	copy_df = input_production[input_production["Centrala"] == "Abator_Oiejdea"].copy()
	copy_df['Centrala'] = "F4"

	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	# Copying the data for Ciugud PVPP
	copy_df = input_production[input_production["Centrala"] == "Abator_Oiejdea"].copy()
	copy_df['Centrala'] = "Ciugud"

	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	# Copying the data for Abator Bocsa PVPP
	copy_df = input_production[input_production["Centrala"] == "Abator_Oiejdea"].copy()
	copy_df['Centrala'] = "Abator_Bocsa"
	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	bocsa_data = pd.read_csv(f"{path}/Bocsa.csv")

	# Completing the Radiatie column for Abator Bocsa
	bocsa_radiatie = bocsa_data["ghi"]
	input_production["Radiatie"][input_production["Centrala"] == "Abator_Bocsa"] = bocsa_radiatie

	# Completing the Temperatura column for Abator Bocsa
	bocsa_temperatura = bocsa_data["air_temp"]
	input_production["Temperatura"][input_production["Centrala"] == "Abator_Bocsa"] = bocsa_temperatura

	# Completing the Nori column for Abator Bocsa
	bocsa_nori = bocsa_data["cloud_opacity"]
	input_production["Nori"][input_production["Centrala"] == "Abator_Bocsa"] = bocsa_nori

	# Copying the data for Lunca PVPP
	copy_df = input_production[input_production["Centrala"] == "Abator_Oiejdea"].copy()
	copy_df['Centrala'] = "F24"
	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	lunca_data = pd.read_csv(f"{path}/Lunca.csv")

	# Completing the Radiatie column for F24
	lunca_radiatie = lunca_data["ghi"]
	input_production["Radiatie"][input_production["Centrala"] == "F24"] = lunca_radiatie

	# Completing the Temperatura column for F24
	lunca_temperatura = lunca_data["air_temp"]
	input_production["Temperatura"][input_production["Centrala"] == "F24"] = lunca_temperatura

	# Completing the Nori column for F24
	lunca_nori = lunca_data["cloud_opacity"]
	input_production["Nori"][input_production["Centrala"] == "F24"] = lunca_nori

	# Copying the data for Brasov PVPP
	copy_df = input_production[input_production["Centrala"] == "Abator_Oiejdea"].copy()
	copy_df['Centrala'] = "Brasov"
	# Append the copied dataframe to the original dataframe
	input_production = pd.concat([input_production, copy_df])

	brasov_data = pd.read_csv(f"{path}/Brasov.csv")

	# Completing the Radiatie column for Brasov
	brasov_radiatie = brasov_data["ghi"]
	input_production["Radiatie"][input_production["Centrala"] == "Brasov"] = brasov_radiatie

	# Completing the Temperatura column for Brasov
	brasov_temperatura = brasov_data["air_temp"]
	input_production["Temperatura"][input_production["Centrala"] == "Brasov"] = brasov_temperatura

	# Completing the Nori column for Brasov
	brasov_nori = brasov_data["cloud_opacity"]
	input_production["Nori"][input_production["Centrala"] == "Brasov"] = brasov_nori

	# Saving input_production to Excel
	input_production.to_excel("./Transavia/Production/Input_production_filled.xlsx", index=False)

# ===================================================================================TRANSAVIA FORECAST==================================================================================================================
def cleaning_input_files():
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx")
	input_santimbru = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	input_brasov[:] = ""
	input_santimbru[:] = ""
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)
	input_santimbru.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

# Creating the Input file for Santimbru region===========================
def creating_input_consumption_Santimbru():
	input = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	santimbru_data = pd.read_csv("./Transavia/data/Santimbru.csv")
	# Convert 'period_end' in santimbru to datetime
	santimbru_data['period_end'] = pd.to_datetime(santimbru_data['period_end'], errors='coerce')
	# Then, convert the datetime to EET (taking into account DST if applicable)
	santimbru_data['period_end_EET'] = santimbru_data['period_end'].dt.tz_convert('Europe/Bucharest')
	
	# Extract just the date part in the desired format (as strings)
	santimbru_dates = santimbru_data['period_end_EET'].dt.strftime('%Y-%m-%d')
	# Shift the 'period_end' column by 2 hours
	# santimbru_dates['period_end'] = santimbru_data['period_end'] + pd.Timedelta(hours=3)
	# Write the dates from santimbru_dates to input_production.Data
	input['Data'] = santimbru_dates.values
	# Fill NaNs in the 'Data' column with next valid observation
	input['Data'].fillna(method='bfill', inplace=True)
	# Completing the Interval column
	santimbru_intervals = santimbru_data["period_end_EET"].dt.hour
	input["Interval"] = santimbru_intervals
	# Replace NaNs in the 'Interval' column with 0
	input['Interval'].fillna(2, inplace=True)
	# Completing the Temperatura column
	santimbru_temperatura = santimbru_data["air_temp"]
	input["Temperatura"] = santimbru_temperatura
	# Filling the IBD column
	input["IBD"] = "Abator"
	# Filling the Locatie column
	input["Locatie"] = "Santimbru"
	# Filling the PVPP column
	input["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	input["Data"] = pd.to_datetime(input["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	input['Lookup'] = input["Data"].dt.strftime('%d.%m.%Y') + str("F2")
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	main_df.to_excel('./Transavia/Consumption/Input_flow_Chicks.xlsx', index=False)

	# Filling the data for Ciugud
	ciugud_data = input[input["IBD"] == "Abator"].copy()
	ciugud_data["IBD"] = "Ciugud"
	ciugud_data["Flow_Chicks"] = ""
	ciugud_data["Lookup"] = ""
	input = pd.concat([input, ciugud_data])

	# Filling the data for F20-F21
	f20_f21_data = input[input["IBD"] == "Abator"].copy()
	f20_f21_data["IBD"] = "F20-F21"
	f20_f21_data["PVPP"] = ""
	# Filling the Flow_Chicks column for F20-F21
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f20_f21_data['Lookup'] = f20_f21_data["Data"].dt.strftime('%d.%m.%Y') + str("F20")
	input = pd.concat([input, f20_f21_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F3
	f3_data = input[input["IBD"] == "Abator"].copy()
	f3_data["IBD"] = "F3"
	f3_data["PVPP"] = ""
	# Filling the Flow_Chicks column for F3
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f3_data['Lookup'] = f3_data["Data"].dt.strftime('%d.%m.%Y') + str("F3")
	input = pd.concat([input, f3_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F4
	f4_data = input[input["IBD"] == "Abator"].copy()
	f4_data["IBD"] = "F4"
	# Filling the Flow_Chicks column for F4
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f4_data['Lookup'] = f4_data["Data"].dt.strftime('%d.%m.%Y') + str("F4")
	input = pd.concat([input, f4_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F5
	f5_data = input[input["IBD"] == "Abator"].copy()
	f5_data["IBD"] = "F5"
	# Filling the Flow_Chicks column for F5
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f5_data['Lookup'] = f5_data["Data"].dt.strftime('%d.%m.%Y') + str("F5")
	input = pd.concat([input, f5_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F7
	f7_data = input[input["IBD"] == "Abator"].copy()
	f7_data["IBD"] = "F7"
	f7_data["PVPP"] = ""
	# Filling the Flow_Chicks column for F7
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f7_data['Lookup'] = f7_data["Data"].dt.strftime('%d.%m.%Y') + str("F7")
	input = pd.concat([input, f7_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for FNC
	fnc_data = input[input["IBD"] == "Abator"].copy()
	fnc_data["IBD"] = "FNC"
	# Filling the Flow_Chicks column for FNC
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	fnc_data['Lookup'] = fnc_data["Data"].dt.strftime('%d.%m.%Y') + str("FNC")
	input = pd.concat([input, fnc_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = main_df.copy()
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for Abator Bocsa
	bocsa_data = pd.read_csv("./Transavia/data/Bocsa.csv")
	abator_bocsa_data = input[input["IBD"] == "Abator"].copy()
	abator_bocsa_data["IBD"] = "Abator_Bocsa"
	abator_bocsa_data["Locatie"] = "Bocsa"
	# Completing the Temperatura column
	bocsa_temperatura = bocsa_data["air_temp"]
	abator_bocsa_data["Temperatura"] = bocsa_temperatura
	# Completing the Radiatie column
	# bocsa_ghi = bocsa_data["ghi"]
	# abator_bocsa_data["Radiatie"] = bocsa_ghi
	# Filling the Flow_Chicks column for Abator Bocsa
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	abator_bocsa_data['Lookup'] = abator_bocsa_data["Data"].dt.strftime('%d.%m.%Y') + str("F15S1")
	abator_bocsa_data['Lookup2'] = abator_bocsa_data["Data"].dt.strftime('%d.%m.%Y') + str("F22")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Bocsa')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = abator_bocsa_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict) + main_df['Lookup2'].map(lookup_dict)
	input = pd.concat([input, abator_bocsa_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for Ferma_Bocsa
	ferma_bocsa_data = input[input["IBD"] == "Abator_Bocsa"].copy()
	ferma_bocsa_data["IBD"] = "Ferma_Bocsa"
	# Filling the Flow_Chicks column for Ferma Bocsa
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	ferma_bocsa_data['Lookup'] = ferma_bocsa_data["Data"].dt.strftime('%d.%m.%Y') + str("F15S2")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Bocsa')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = ferma_bocsa_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = pd.concat([input, ferma_bocsa_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F_Cristian
	cristian_data = pd.read_csv("./Transavia/data/Cristian.csv")
	f_cristian_data = input[input["IBD"] == "Abator"].copy()
	f_cristian_data["IBD"] = "F_Cristian"
	f_cristian_data["Locatie"] = "Cristian"
	f_cristian_data["PVPP"] = ""
	f_cristian_data["Flow_Chicks"] = ""
	f_cristian_data["Lookup"] = ""
	# Completing the Temperatura column
	cristian_temperatura = cristian_data["air_temp"]
	f_cristian_data["Temperatura"] = cristian_temperatura
	# Completing the Radiatie column
	cristian_ghi = cristian_data["ghi"]
	cristian_data["Radiatie"] = ""
	input = pd.concat([input, f_cristian_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F10
	cristuru_data = pd.read_csv("./Transavia/data/Cristuru.csv")
	f10_data = input[input["IBD"] == "Abator"].copy()
	f10_data["IBD"] = "F10"
	f10_data["Locatie"] = "Cristuru Secuiesc"
	f10_data["PVPP"] = ""
	# Completing the Temperatura column
	cristuru_temperatura = cristuru_data["air_temp"]
	f10_data["Temperatura"] = cristuru_temperatura
	# Filling the Flow_Chicks column for F10
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f10_data['Lookup'] = f10_data["Data"].dt.strftime('%d.%m.%Y') + str("F10")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = f10_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = pd.concat([input, f10_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for Jebel1
	jebel_data = pd.read_csv("./Transavia/data/Jebel.csv")
	jebel1_data = input[input["IBD"] == "Abator"].copy()
	jebel1_data["IBD"] = "Jebel1"
	jebel1_data["Locatie"] = "Jebel"
	jebel1_data["PVPP"] = ""
	jebel1_data["Flow_Chicks"] = ""
	jebel1_data["Lookup"] = ""
	# Completing the Temperatura column
	jebel_temperatura = jebel_data["air_temp"]
	jebel1_data["Temperatura"] = jebel_temperatura
	input = pd.concat([input, jebel1_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F6
	lunca_data = pd.read_csv("./Transavia/data/Lunca.csv")
	f6_data = input[input["IBD"] == "Abator"].copy()
	f6_data["IBD"] = "F6"
	f6_data["Locatie"] = "Lunca Muresului"
	f6_data["PVPP"] = ""
	# Completing the Temperatura column
	lunca_temperatura = lunca_data["air_temp"]
	f6_data["Temperatura"] = lunca_temperatura
	# Filling the Flow_Chicks column for F6
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f6_data['Lookup'] = f6_data["Data"].dt.strftime('%d.%m.%Y') + str("F6")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = f6_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = pd.concat([input, f6_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F17
	medias_data = pd.read_csv("./Transavia/data/Medias.csv")
	f17_data = input[input["IBD"] == "Abator"].copy()
	f17_data["IBD"] = "F17"
	f17_data["Locatie"] = "Medias"
	f17_data["PVPP"] = ""
	f17_data["Flow_Chicks"] = ""
	f17_data["Lookup"] = ""
	# Completing the Temperatura column
	medias_temperatura = medias_data["air_temp"]
	f17_data["Temperatura"] = medias_temperatura
	input = pd.concat([input, f17_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F9
	miercurea_data = pd.read_csv("./Transavia/data/Miercurea.csv")
	f9_data = input[input["IBD"] == "Abator"].copy()
	f9_data["IBD"] = "F9"
	f9_data["Locatie"] = "Miercurea Sibiului"
	f9_data["PVPP"] = ""
	# Completing the Temperatura column
	miercurea_temperatura = miercurea_data["air_temp"]
	f9_data["Temperatura"] = miercurea_temperatura
	# Filling the Flow_Chicks column for F9
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f9_data['Lookup'] = f9_data["Data"].dt.strftime('%d.%m.%Y') + str("F9")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = f9_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = pd.concat([input, f9_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)

	# Filling the data for F8
	lunca_data = pd.read_csv("./Transavia/data/Lunca.csv")
	f8_data = input[input["IBD"] == "Abator"].copy()
	f8_data["IBD"] = "F8"
	f8_data["Locatie"] = "Lunca Muresului"
	f8_data["PVPP"] = ""
	# Completing the Temperatura column
	lunca_temperatura = lunca_data["air_temp"]
	f8_data["Temperatura"] = lunca_temperatura
	# Filling the Flow_Chicks column for F8
	# Adding the Lookup column to the Input.xlsx file
	# Create the 'Lookup' column by concatenating the 'Data' and 'IBD' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	f8_data['Lookup'] = f8_data["Data"].dt.strftime('%d.%m.%Y') + str("F8")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Alba')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = f8_data
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Temperatures values
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	input = pd.concat([input, f8_data])
	input.to_excel("./Transavia/Consumption/Input/Input.xlsx", index=False)


	return input

# Creating the Input file for Brasov region====================================================
def creating_input_cons_file_Brasov():
	# Filling the data for 594020100002383007
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx")
	brasov_data = pd.read_csv("./Transavia/data/Brasov.csv")
	# Convert 'period_end' in santimbru to datetime
	brasov_data['period_end'] = pd.to_datetime(brasov_data['period_end'], errors='coerce')
	# Then, convert the datetime to EET (taking into account DST if applicable)
	brasov_data['period_end_EET'] = brasov_data['period_end'].dt.tz_convert('Europe/Bucharest')
	# Extract just the date part in the desired format (as strings)
	brasov_dates = brasov_data['period_end_EET'].dt.strftime('%Y-%m-%d')
	# Shift the 'period_end' column by 3 hours
	# brasov_dates['period_end'] = brasov_data['period_end'] + pd.Timedelta(hours=3)
	# Write the dates from santimbru_dates to input_production.Data
	input_brasov['Data'] = brasov_dates.values
	# Fill NaNs in the 'Data' column with next valid observation
	input_brasov['Data'].fillna(method='bfill', inplace=True)
	# Completing the Interval column
	brasov_intervals = brasov_data["period_end_EET"].dt.hour
	input_brasov["Interval"] = brasov_intervals
	# Replace NaNs in the 'Interval' column with 0
	input_brasov['Interval'].fillna(2, inplace=True)
	# Completing the Temperatura column
	brasov_temperatura = brasov_data["air_temp"]
	input_brasov["Temperatura"] = brasov_temperatura
	# Filling the IBD column
	input_brasov["POD"] = "594020100002383007"
	# Filling the PVPP column
	input_brasov["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	input_brasov["Data"] = pd.to_datetime(input_brasov["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	input_brasov['Lookup'] = input_brasov["Data"].dt.strftime('%d.%m.%Y') + str("F25")
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	main_df = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx")
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	main_df['Flow_Chicks'] = main_df['Lookup'].map(lookup_dict)
	main_df.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Preserving the POD column format
	from openpyxl import Workbook
	from openpyxl.utils.dataframe import dataframe_to_rows

	wb = Workbook()
	ws = wb.active

	for r in dataframe_to_rows(main_df, index=False, header=True):
		ws.append(r)

	# Assuming the 'POD' column is the first column, starting from the second row
	for cell in ws['E'][1:]:  # Skip header row
		cell.value = str(cell.value)  # Reinforce string format
		cell.number_format = '@'

	wb.save("./Transavia/Consumption/output_formatted.xlsx")

	# Filling the data for 594020100002224041
	input_brasov = pd.read_excel("./Transavia/Consumption/output_formatted.xlsx")
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002383007"].copy()
	new_data["POD"] = "594020100002224041"
	new_data["PVPP"] = ""
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

	# Filling the data for 594020100002273568
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002273568"
	new_data["PVPP"] = ""
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

	# Filling the data for 594020100002382970
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002382970"
	new_data["PVPP"] = 1
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

	# Filling the data for 594020100002383014
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002383014"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F30/S1")
	new_data['Lookup2'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F30/S2")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict) + new_data['Lookup2'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002383069
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002383069"
	new_data["PVPP"] = 1
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

	# Filling the data for 594020100002383502
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002383502"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F26")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002383519
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002383519"
	# Filling the PVPP column
	new_data["PVPP"] = ""
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F27")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002384233
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002384233"
	new_data["PVPP"] = ""
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

	# Filling the data for 594020100002384691
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002384691"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F31")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002836497
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002836497"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F27")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002841279
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002841279"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F29")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020100002967269
	input_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx").copy()
	print(input_brasov)
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020100002967269"
	# Filling the PVPP column
	new_data["PVPP"] = 1
	# Filling the Flow_Chicks column
	# Adding the Lookup column to the Input.xlsx file
	# Ensure the 'Data' column is in datetime format
	new_data["Data"] = pd.to_datetime(new_data["Data"])
	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	new_data['Lookup'] = new_data["Data"].dt.strftime('%d.%m.%Y') + str("F28")
	# Adding the Lookup column to the Fluxuri_pui.xlsx file
	df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsm", sheet_name='Brasov')

	# Ensure the 'Data' column is in datetime format
	df["Data"] = pd.to_datetime(df["Data"])

	# Create the 'Lookup' column by concatenating the 'Data' and 'Interval' columns
	# Format the 'Data' column as a string in 'dd.mm.yyyy' format for concatenation
	df['Lookup'] = df["Data"].dt.strftime('%d.%m.%Y') + df["Loc"].astype(str)
	df.to_excel("./Transavia/Consumption/Fluxuri_pui.xlsx", index=False)
	# Mapping the Flow_Chicks column fo the input
	lookup_df = pd.read_excel("./Transavia/Consumption/Fluxuri_pui.xlsx")
	# Create a dictionary from lookup_df for efficient lookup
	lookup_dict = lookup_df.set_index("Lookup")["Fluxuri_Input"].to_dict()
	# Perform the lookup by mapping the 'Lookup' column in main_df to the values in lookup_dict
	new_data['Flow_Chicks'] = new_data['Lookup'].map(lookup_dict)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel('./Transavia/Consumption/Input/Input_Brasov.xlsx', index=False)

	# Filling the data for 594020300002359730
	input_brasov["POD"] = input_brasov["POD"].astype(str)
	new_data = input_brasov[input_brasov["POD"] == "594020100002224041"].copy()
	new_data["POD"] = "594020300002359730"
	new_data["PVPP"] = ""
	new_data["Flow_Chicks"] = ""
	new_data["Lookup"] = ""
	print(new_data)
	input_brasov = pd.concat([input_brasov, new_data])
	input_brasov.to_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx", index=False)

def predicting_exporting_Transavia(dataset):
	datasets_forecast = dataset.copy()
	CEFs = datasets_forecast.Centrala.unique()
	dataset_forecast = {elem : pd.DataFrame for elem in CEFs}
	for CEF in CEFs:
		print("Predicting for {}".format(CEF))
		xgb_loaded = joblib.load("./Transavia/Production/Models/rs_xgb_{}.pkl".format(CEF))
		dataset_forecast = datasets_forecast[:][datasets_forecast.Centrala == CEF]
		dataset_forecast["Data"] = pd.to_datetime(dataset_forecast["Data"])
		dataset_forecast["Month"] = dataset_forecast.Data.dt.month
		if CEF in ["F24"]:
			df_forecast = dataset_forecast.drop(["Data", "Nori", "Centrala"], axis=1)
		else:
			df_forecast = dataset_forecast.drop(["Data", "Centrala"], axis=1)
		preds = xgb_loaded.predict(df_forecast.values)
		# Exporting Results to Excel
		workbook = xlsxwriter.Workbook("./Transavia/Production/Results/Results_daily_{}.xlsx".format(CEF))
		worksheet = workbook.add_worksheet("Prediction_Production")

		worksheet.write(0,0,"Data")
		worksheet.write(0,1,"Interval")
		worksheet.write(0,2,"Production")
		date_format = workbook.add_format({'num_format':'dd.mm.yyyy'})
		row = 1
		col = 0
		for value in preds:
		  worksheet.write(row, col+2, value)
		  row +=1
		row = row - len(preds)
		for data in dataset_forecast["Data"]:
		  worksheet.write(row, col, data, date_format)
		  row +=1
		row = row - len(dataset_forecast["Data"])
		for value in dataset_forecast["Interval"]:
		  worksheet.write(row, col+1, value)
		  row +=1
		workbook.close()

def predicting_exporting_consumption_Santimbru(dataset):
	# Importing the dataset
	dataset_forecast = dataset
	IBDs = dataset_forecast.IBD.unique()
	IBDs_PVPP = ["Abator", "Abator_Bocsa", "Ciugud", "F5", "Ferma_Bocsa", "FNC", "F4"]
	datasets_forecast = {elem : pd.DataFrame for elem in IBDs}
	for IBD in IBDs:
		datasets_forecast[IBD] = dataset_forecast[:][dataset_forecast.IBD == IBD]
		datasets_forecast[IBD]["WeekDay"] = datasets_forecast[IBD].Data.dt.weekday
		datasets_forecast[IBD]["Month"] = datasets_forecast[IBD].Data.dt.month
		datasets_forecast[IBD]["Holiday"] = 0
		for holiday in datasets_forecast[IBD]["Data"].unique():
			if holiday in holidays.ds.values:
				datasets_forecast[IBD]["Holiday"][datasets_forecast[IBD]["Data"] == holiday] = 1
		if len(datasets_forecast[IBD]["Flow_Chicks"].value_counts()) > 0 and len(datasets_forecast[IBD]["Radiatie"].value_counts()) > 0:
			## Restructuring the dataset
			datasets_forecast[IBD] = datasets_forecast[IBD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "Flow_Chicks", "Radiatie"]]
		elif len(datasets_forecast[IBD]["Flow_Chicks"].value_counts()) > 0:
			datasets_forecast[IBD] = datasets_forecast[IBD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "Flow_Chicks"]]
		elif len(datasets_forecast[IBD]["Radiatie"].value_counts()) > 0:
			datasets_forecast[IBD] = datasets_forecast[IBD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "Radiatie"]]
		else:
		  datasets_forecast[IBD] = datasets_forecast[IBD][["Month", "WeekDay","Holiday", "Interval", "Temperatura"]]
	# datasets_forecast[IBD].replace([np.inf, -np.inf], np.nan)
	# datasets_forecast[IBD].dropna(inplace = True)
		# Check if the cons place has PVPP and add the column, if it does
		if IBD in IBDs_PVPP:
			datasets_forecast[IBD]["PVPP"] = 1
	# Predicting
	predictions = {}
	for IBD in datasets_forecast.keys():
		if IBD not in IBDs_PVPP:
			xgb_loaded = joblib.load("./Transavia/Consumption/Models_PVPP/rs_xgb_{}.pkl".format(IBD))
			print("Predicting for {}".format(IBD))
			# st.write(datasets_forecast[IBD])
			xgb_preds = xgb_loaded.predict(datasets_forecast[IBD].values)
			predictions[IBD] = xgb_preds
			predictions["Data"] = dataset_forecast["Data"]
			predictions["Interval"] = dataset_forecast["Interval"]
	# Predicting with PVPP models
	predictions_PVPP = {}
	for IBD in datasets_forecast.keys():
		if IBD in IBDs_PVPP:
			if os.path.isfile("./Transavia/Consumption/Models_PVPP/rs_xgb_{}_PVPP.pkl".format(IBD)):
				xgb_loaded = joblib.load("./Transavia/Consumption/Models_PVPP/rs_xgb_{}_PVPP.pkl".format(IBD))
				print("Predicting for {}".format(IBD))
				# st.write(datasets_forecast[IBD])
				xgb_preds = xgb_loaded.predict(datasets_forecast[IBD].values)
				predictions_PVPP[IBD] = xgb_preds
				predictions_PVPP["Data"] = dataset_forecast["Data"]
				predictions_PVPP["Interval"] = dataset_forecast["Interval"]
	# Exporting Results to Excel
	workbook = xlsxwriter.Workbook("./Transavia/Consumption/Results/XGB/Results_IBDs_daily.xlsx")
	worksheet = workbook.add_worksheet("Prediction_Consumption")

	worksheet.write(0,0,"Data")
	worksheet.write(0,1,"Interval")
	worksheet.write(0,2,"Prediction")
	worksheet.write(0,3,"IBD")
	worksheet.write(0,4,"Lookup")
	date_format = workbook.add_format({'num_format':'dd.mm.yyyy'})
	row = 1
	col = 0
	for IBD in datasets_forecast.keys():
		if IBD in predictions_PVPP.keys():
			for value in predictions_PVPP[IBD]:
				worksheet.write(row, col+2, value)
				worksheet.write(row, col+3, IBD)
				row +=1
		else:
			for value in predictions[IBD]:
				worksheet.write(row, col+2, value)
				worksheet.write(row, col+3, IBD)
				row +=1
	row = row - len(predictions[IBD])*(len(datasets_forecast.keys()))
	for data in dataset_forecast["Data"]:
		worksheet.write(row, col, datetime.date(data),date_format)
		worksheet.write_formula(row, col+4, "=A"+ str(row+1)+ "&" + "B"+ str(row+1)+ "&" + "D" + str(row+1))
		row +=1
	row = row - len(predictions["Data"])
	for interval in predictions["Interval"]:
		worksheet.write(row, col+1, interval)
		row +=1
		# row = 1
		# for value in y_test:
		#     worksheet.write(row, col + 1, value)
		#     row +=1
	workbook.close()

def predicting_exporting_consumption_Brasov(dataset):
	# Importing the dataset
	dataset_forecast = dataset
	PODs = dataset_forecast.POD.unique()
	datasets_forecast = {elem : pd.DataFrame for elem in PODs}
	for POD in PODs:
		datasets_forecast[POD] = dataset_forecast[:][dataset_forecast.POD == POD]
		datasets_forecast[POD]["WeekDay"] = datasets_forecast[POD].Data.dt.weekday
		datasets_forecast[POD]["Month"] = datasets_forecast[POD].Data.dt.month
		datasets_forecast[POD]["Holiday"] = 0
		for holiday in datasets_forecast[POD]["Data"].unique():
		  if holiday in holidays.ds.values:
			  datasets_forecast[POD]["Holiday"][datasets_forecast[POD]["Data"] == holiday] = 1
		if len(datasets_forecast[POD]["Flow_Chicks"].value_counts()) > 0 and len(datasets_forecast[POD]["PVPP"].value_counts()) > 0:
			datasets_forecast[POD] = datasets_forecast[POD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "Flow_Chicks", "PVPP"]]
		elif len(datasets_forecast[POD]["Flow_Chicks"].value_counts()) > 0:
			datasets_forecast[POD] = datasets_forecast[POD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "Flow_Chicks"]]
		elif len(datasets_forecast[POD]["PVPP"].value_counts()) > 0:
			datasets_forecast[POD] = datasets_forecast[POD][["Month", "WeekDay","Holiday", "Interval", "Temperatura", "PVPP"]]
		else:
			datasets_forecast[POD] = datasets_forecast[POD][["Month", "WeekDay","Holiday", "Interval", "Temperatura"]]
		# datasets_forecast[POD].replace([np.inf, -np.inf], np.nan)
		# datasets_forecast[POD].dropna(inplace = True)
	
	# Predicting noPVPP
	predictions = {}
	for POD in datasets_forecast.keys():
		if "PVPP" in datasets_forecast[POD].columns:
			xgb_loaded = joblib.load("./Transavia/Consumption/Models_PVPP/Brasov_Models/rs_xgb_{}.pkl".format(POD))
			print("Predicting for {}".format(POD))
			# st.write(datasets_forecast[POD])
			xgb_preds = xgb_loaded.predict(datasets_forecast[POD].values)
			predictions[POD] = xgb_preds
			predictions["Data"] = dataset_forecast["Data"]
			predictions["Interval"] = dataset_forecast["Interval"]
		else:
			xgb_loaded = joblib.load("./Transavia/Consumption/Models_PVPP/Brasov_Models/rs_xgb_{}.pkl".format(POD))
			print("Predicting for without PVPP{}".format(POD))
			# st.write(datasets_forecast[POD])
			xgb_preds = xgb_loaded.predict(datasets_forecast[POD].values)
			predictions[POD] = xgb_preds
			predictions["Data"] = dataset_forecast["Data"]
			predictions["Interval"] = dataset_forecast["Interval"]
	# Predicting with PVPP
	predictions_PVPP = {}
	for POD in datasets_forecast.keys():
		if os.path.isfile(".Transavia/Consumption/Brasov_Models/rs_xgb_{}_PVPP.pkl".format(POD)):
		  xgb_loaded = joblib.load("./Transavia/Consumption/Brasov_Models/rs_xgb_{}_PVPP.pkl".format(POD))
		  print("Predicting for {}".format(POD))
		  # print(datasets_forecast[POD])
		  xgb_preds = xgb_loaded.predict(datasets_forecast[POD].values)
		  predictions_PVPP[POD] = xgb_preds
		  predictions_PVPP["Data"] = dataset_forecast["Data"]
		  predictions_PVPP["Interval"] = dataset_forecast["Interval"]

	# Exporting Results to Excel
	workbook = xlsxwriter.Workbook("./Transavia/Consumption/Results/XGB/Results_IBDs_daily_Brasov.xlsx")
	worksheet = workbook.add_worksheet("Prediction_Consumption")

	worksheet.write(0,0,"Data")
	worksheet.write(0,1,"Interval")
	worksheet.write(0,2,"Prediction")
	worksheet.write(0,3,"POD")
	worksheet.write(0,4,"Lookup")
	date_format = workbook.add_format({'num_format':'dd.mm.yyyy'})
	row = 1
	col = 0
	for POD in datasets_forecast.keys():
		if POD in predictions_PVPP.keys():
			for value in predictions_PVPP[POD]:
				worksheet.write(row, col+2, abs(value))
				worksheet.write(row, col+3, str(POD))
				row +=1
		else:
			for value in predictions[POD]:
				worksheet.write(row, col+2, abs(value))
				worksheet.write(row, col+3, str(POD))
				row +=1
	row = row - len(predictions[POD])*(len(datasets_forecast.keys()))
	for data in dataset_forecast["Data"]:
		worksheet.write(row, col, datetime.date(data),date_format)
		worksheet.write_formula(row, col+4, "=A"+ str(row+1)+ "&" + "B"+ str(row+1)+ "&" + "D" + str(row+1))
		row +=1
	row = row - len(predictions["Data"])
	for interval in predictions["Interval"]:
		worksheet.write(row, col+1, interval)
		row +=1
		# row = 1
		# for value in y_test:
		#     worksheet.write(row, col + 1, value)
		#     row +=1
	workbook.close()

def zip_files(folder_path, zip_name):
	zip_path = os.path.join(folder_path, zip_name)
	with zipfile.ZipFile(zip_path, 'w') as zipf:
		for root, _, files in os.walk(folder_path):
			for file in files:
				if file != zip_name:  # Avoid zipping the zip file itself
					file_path = os.path.join(root, file)
					arcname = os.path.relpath(file_path, folder_path)  # Relative path within the zip file
					zipf.write(file_path, arcname)

# Creating the dictionary for the Production PVPPs locations
locations_PVPPs = {"Lunca": {"lat": 46.427350, "lon": 23.905963}, "Brasov": {"lat": 45.642680, "lon": 25.588725},
					"Santimbru": {"lat":46.135244 , "lon":23.644428 }, "Bocsa": {"lat":45.377012 , "lon":21.718752}}

def render_production_forecast_Transavia(locations_PVPPs):
	st.write("Production Forecast")
	# ... (other content and functionality for production forecasting)
	# Iterating through the dictionary of PVPP locations
	for location in locations_PVPPs.keys():
		print("Getting data for {}".format(location))
		output_path = f"./Transavia/data/{location}.csv"
		lat = locations_PVPPs[location]["lat"]
		lon = locations_PVPPs[location]["lon"]
		fetch_data(lat, lon, solcast_api_key, output_path)
		# Adjusting the values to EET time
		data = pd.read_csv(f"./Transavia/data/{location}.csv")
		# # Assuming 'period_end' is the column to keep fixed and all other columns are to be shifted
		# columns_to_shift = data.columns.difference(['period_end'])

		# # Shift the data columns by 2 intervals
		# data_shifted = data[columns_to_shift].shift(3)

		# # Combine the fixed 'period_end' with the shifted data columns
		# data_adjusted = pd.concat([data[['period_end']], data_shifted], axis=1)

		# # Optionally, handle the NaN values in the first two rows after shifting
		# data_adjusted.fillna(0, inplace=True)  # Or use another method as appropriate

		# # Save the adjusted DataFrame
		# data_adjusted.to_csv(f"./Transavia/data/{location}.csv", index=False)
	# Creating the input_production file
	path = "./Transavia/data"
	creating_input_production_file(path)
	df = pd.read_excel("./Transavia/Production/Input_production_filled.xlsx")
	# uploaded_files = st.file_uploader("Choose a file", type=["text/csv", "xlsx"], accept_multiple_files=True)

	# if uploaded_files is not None:
	# 	for uploaded_file in uploaded_files:
	# 		if uploaded_file.type == "text/csv":
	# 			df = pd.read_csv(uploaded_file)
	# 		elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
	# 			try:
	# 				df = pd.read_excel(uploaded_file)
	# 			except ValueError:
	# 				st.error("Expected sheet name 'Forecast_Dataset' not found in Excel file.")
	# 				continue
	# 		else:
	# 			st.error("Unsupported file format. Please upload a CSV or XLSX file.")
	# 			continue
	st.dataframe(df)
	# Submit button
	if st.button('Submit'):
		st.success('Forecast Ready', icon="✅")
		# Your code to generate the forecast
		predicting_exporting_Transavia(df)
		print("Forecast is on going...")
		# Creating the ZIP file with the Productions:
		folder_path = './Transavia/Production/Results'
		zip_name = 'Transavia_Production_Results.zip'
		zip_files(folder_path, zip_name)
		file_path = './Transavia/Production/Results/Transavia_Production_Results.zip'

		with open(file_path, "rb") as f:
			zip_data = f.read()

		# Create a download link
		b64 = base64.b64encode(zip_data).decode()
		button_html = f"""
			 <a download="Transavia_Production_Results.zip" href="data:application/zip;base64,{b64}" download>
			 <button kind="secondary" data-testid="baseButton-secondary" class="st-emotion-cache-12tniow ef3psqc12">Download Forecast Results</button>
			 </a> 
			 """
		st.markdown(button_html, unsafe_allow_html=True)

# Creating the dictionary for the Transavia locations
locations_cons = {"Lunca": {"lat": 46.427350, "lon": 23.905963}, "Brasov": {"lat": 45.642680, "lon": 25.588725},
					"Santimbru": {"lat":46.135244 , "lon":23.644428 }, "Bocsa": {"lat":45.377012 , "lon":21.718752}, "Cristian": {"lat":45.782114 , "lon":24.029499},
					"Cristuru": {"lat":46.292453 , "lon":25.031714}, "Jebel": {"lat":45.562394 , "lon":21.214496}, "Medias": {"lat":46.157283 , "lon":24.347167},
					"Miercurea": {"lat":45.890054 , "lon":23.791766}}

# Netting the consumotion with the productions
def netting_consumption_with_productions():
	df_santimbru = pd.read_excel("./Transavia/Consumption/Results/XGB/Results_IBDs_daily.xlsx")
	df_brasov = pd.read_excel("./Transavia/Consumption/Results/XGB/Results_IBDs_daily_Brasov.xlsx")
	df_production_abator_oiejdea = pd.read_excel("./Transavia/Production/Results/Results_daily_Abator_Oiejdea.xlsx")
	df_production_abator_bocsa = pd.read_excel("./Transavia/Production/Results/Results_daily_Abator_Bocsa.xlsx")
	df_production_ciugud = pd.read_excel("./Transavia/Production/Results/Results_daily_Ciugud.xlsx")
	df_production_ferma_bocsa = pd.read_excel("./Transavia/Production/Results/Results_daily_Ferma_Bocsa.xlsx")
	df_production_fnc = pd.read_excel("./Transavia/Production/Results/Results_daily_FNC.xlsx")
	df_production_f4 = pd.read_excel("./Transavia/Production/Results/Results_daily_F4.xlsx")
	df_production_f24 = pd.read_excel("./Transavia/Production/Results/Results_daily_F24.xlsx")
	df_production_f5 = 0.074/df_production_f4*0.0625
	df_production_brasov = pd.read_excel("./Transavia/Production/Results/Results_daily_Brasov.xlsx")
	
	# Extracting the Abator Oiejdea production from the Abator Oiejdea Consumption place
	df_santimbru[[df_santimbru["IBD"] == "Abator"] & [df_santimbru["Data"] == df_production_abator_oiejdea["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "Abator"] & [df_santimbru["Data"] == df_production_abator_oiejdea["Data"]]] - df_production_abator_oiejdea[df_production_abator_oiejdea["Data"] == df_santimbru["Data"]]["Production"]
	# Extracting the F4 production from the F4 Consumption place
	df_santimbru[[df_santimbru["IBD"] == "F4"] & [df_santimbru["Data"] == df_production_f4["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "F4"] & [df_santimbru["Data"] == df_production_f4["Data"]]] - df_production_f4[df_production_f4["Data"] == df_santimbru["Data"]]["Production"]
	# Extracting the F5 production from the F5 Consumption place
	df_santimbru[[df_santimbru["IBD"] == "F5"] & [df_santimbru["Data"] == df_production_f5["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "F5"] & [df_santimbru["Data"] == df_production_f5["Data"]]] - df_production_f5[df_production_f5["Data"] == df_santimbru["Data"]]["Production"]
    # Extracting the FNC production from the FNC Consumption place
	df_santimbru[[df_santimbru["IBD"] == "FNC"] & [df_santimbru["Data"] == df_production_fnc["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "FNC"] & [df_santimbru["Data"] == df_production_fnc["Data"]]] - df_production_fnc[df_production_fnc["Data"] == df_santimbru["Data"]]["Production"]
    # Extracting the Ciugud production from the Ciugud Consumption place
	df_santimbru[[df_santimbru["IBD"] == "Ciugud"] & [df_santimbru["Data"] == df_production_ciugud["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "Ciugud"] & [df_santimbru["Data"] == df_production_ciugud["Data"]]] - df_production_ciugud[df_production_ciugud["Data"] == df_santimbru["Data"]]["Production"]
	# Extracting the Abator Bocsa production from the Abator Bocsa Consumption place
	df_santimbru[[df_santimbru["IBD"] == "Abator_Bocsa"] & [df_santimbru["Data"] == df_production_abator_bocsa["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "Abator_Bocsa"] & [df_santimbru["Data"] == df_production_abator_bocsa["Data"]]] - df_production_abator_bocsa[df_production_abator_bocsa["Data"] == df_santimbru["Data"]]["Production"]
	# Extracting the Ferma Bocsa production from the Ferma Bocsa Consumption place
	df_santimbru[[df_santimbru["IBD"] == "Ferma_Bocsa"] & [df_santimbru["Data"] == df_production_ferma_bocsa["Data"]]] = df_santimbru[[df_santimbru["IBD"] == "Ferma_Bocsa"] & [df_santimbru["Data"] == df_production_ferma_bocsa["Data"]]] - df_production_ferma_bocsa[df_production_ferma_bocsa["Data"] == df_santimbru["Data"]]["Production"]*162/1721
	# Extracting the Abator Brasov production from the Abator Brasov Consumption place
	df_brasov[[df_brasov["POD"] == 594020100002382970] & [df_brasov["Data"] == df_production_brasov["Data"]]] = df_brasov[[df_brasov["POD"] == 594020100002382970] & [df_brasov["Data"] == df_production_brasov["Data"]]] - df_production_brasov[df_production_brasov["Data"] == df_brasov["Data"]]["Production"]*0.24
    
    # Extracting the F25 production from the F25 Consumption place
	df_brasov[[df_brasov["POD"] == 594020100002383007] & [df_brasov["Data"] == df_production_brasov["Data"]]] = df_brasov[[df_brasov["POD"] == 594020100002383007] & [df_brasov["Data"] == df_production_brasov["Data"]]] - df_production_brasov[df_production_brasov["Data"] == df_brasov["Data"]]["Production"]*300/3400
    
    # Extracting the F26 production from the F26 Consumption place
	df_brasov[[df_brasov["POD"] == 594020100002383502] & [df_brasov["Data"] == df_production_brasov["Data"]]] = df_brasov[[df_brasov["POD"] == 594020100002383502] & [df_brasov["Data"] == df_production_brasov["Data"]]] - df_production_brasov[df_production_brasov["Data"] == df_brasov["Data"]]["Production"]*500/3400

def render_consumption_forecast_Transavia():
	if st.button("Bring The Data"):
		st.write("Consumption Forecast")
		# ... (other content and functionality for production forecasting)
		# Iterating through the dictionary of PVPP locations
		for location in locations_cons.keys():
			print("Getting data for {}".format(location))
			output_path = f"./Transavia/data/{location}.csv"
			lat = locations_cons[location]["lat"]
			lon = locations_cons[location]["lon"]
			fetch_data(lat, lon, solcast_api_key, output_path)
		# Creating the input_production file
		path = "./Transavia/data"
		cleaning_input_files()
		creating_input_consumption_Santimbru()
		creating_input_cons_file_Brasov()
		df_santimbru = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
		df_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx")

		st.dataframe(df_santimbru)
		st.dataframe(df_brasov)
		# Creating the ZIP file with the Predictions:
		folder_path = './Transavia/Consumption/Input'
		zip_name = 'Transavia_Inputs.zip'
		zip_files(folder_path, zip_name)
		file_path = './Transavia/Consumption/Input/Transavia_Inputs.zip'

		with open(file_path, "rb") as f:
			zip_data = f.read()

		# Create a download link
		b64 = base64.b64encode(zip_data).decode()
		button_html = f"""
			 <a download="Transavia_Inputs.zip" href="data:application/zip;base64,{b64}" download>
			 <button kind="secondary" data-testid="baseButton-secondary" class="st-emotion-cache-12tniow ef3psqc12">Download Input Files</button>
			 </a> 
			 """
		st.markdown(button_html, unsafe_allow_html=True)
	# Submit button
	if st.button('Submit'):
		st.success('Forecast Ready', icon="✅")
		# Your code to generate the forecast
		df_santimbru = pd.read_excel("./Transavia/Consumption/Input/Input.xlsx")
		df_brasov = pd.read_excel("./Transavia/Consumption/Input/Input_Brasov.xlsx")
		predicting_exporting_consumption_Santimbru(df_santimbru)
		predicting_exporting_consumption_Brasov(df_brasov)
		# Creating the ZIP file with the Predictions:
		folder_path = './Transavia/Consumption/Results/XGB'
		zip_name = 'Transavia_Consumption_Results.zip'
		zip_files(folder_path, zip_name)
		file_path = './Transavia/Consumption/Results/XGB/Transavia_Consumption_Results.zip'

		with open(file_path, "rb") as f:
			zip_data = f.read()

		# Create a download link
		b64 = base64.b64encode(zip_data).decode()
		button_html = f"""
			 <a download="Transavia_Consumption_Results.zip" href="data:application/zip;base64,{b64}" download>
			 <button kind="secondary" data-testid="baseButton-secondary" class="st-emotion-cache-12tniow ef3psqc12">Download Forecast Results</button>
			 </a> 
			 """
		st.markdown(button_html, unsafe_allow_html=True)

def render_Transavia_page():
	
	# Web App Title
	st.markdown('''
	## **The Transavia Forecast Section**

	''')

	# Allow the user to choose between Consumption and Production
	forecast_type = st.radio("Choose Forecast Type:", options=["Consumption", "Production"])

	if forecast_type == "Consumption":
		render_consumption_forecast_Transavia()
	elif forecast_type == "Production":
		render_production_forecast_Transavia(locations_PVPPs)
