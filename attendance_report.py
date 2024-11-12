import argparse
import calendar
import json
import logging
import sys
import time
from datetime import datetime

import gspread
import gspread_dataframe
import numpy as np
import pandas as pd
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    datefmt="%Y-%m-%d %H:%M",
)

logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Location parameter")

parser.add_argument("location", type=str, help="run script based on location")

args = parser.parse_args()

LOCATION = args.location

if not LOCATION:
    logger.critical("Location not found values are ['blr', 'pun', 'mum']")
    sys.exit()

# Load the configuration from the JSON file
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

# Access values from the loaded config
if LOCATION not in ["blr", "mum", "pun"]:
    logger.critical("Invalid location values are ['blr', 'pun', 'mum']")
    sys.exit()

FOR_CURRENT_MONTH = config["FOR_CURRENT_MONTH"]

try:
    config = config[LOCATION]
except KeyError:
    logger.critical("Location details not found exiting")
    sys.exit()

sheet_name = config["sheet_name"]
worksheet_name = config["worksheet_name"]
master_sheet_name = config["master_sheet_name"]
master_worksheet_name = config["master_worksheet_name"]
log_sheet_name = config["log_sheet_name"]
log_worksheet_name = config["log_worksheet_name"]
log_attendance_worksheet_name = config["log_attendance_worksheet_name"]

"""**Authorize Google Sheet**"""
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://spreadsheets.google.com/feeds",
]

# Authenticate google sheet with service account
is_auth = Credentials.from_service_account_file("login_cred.json", scopes=scopes)
gc = gspread.authorize(is_auth)

logger.info("Sheet successfully authorized")

"""**Generate Log Report**"""

# Open google sheet
sheet = gc.open(sheet_name)

# Get worksheet
worksheet = sheet.worksheet(worksheet_name)
cols = ["Timestamp", "Email Address", "Full Name", "Select your action"]

# Load sheet data into pandas dataframe with specific columns
log_resp_df = gspread_dataframe.get_as_dataframe(worksheet=worksheet, usecols=cols)

# Drop null rows
log_resp_df.dropna(inplace=True, axis=0, how="all")

# Typecast datetime column from str to datetime
log_resp_df["Timestamp"] = pd.to_datetime(log_resp_df["Timestamp"])

if FOR_CURRENT_MONTH:
    current_month = datetime.now().month
    current_year = datetime.now().year
    log_resp_df = log_resp_df[
        (log_resp_df["Timestamp"].dt.month == current_month)
        & (log_resp_df["Timestamp"].dt.year == current_year)
    ]
    logger.info("Filtered responses for current month")

# Split DateTime and creates new columns as Date and Time in df
log_resp_df[["Date", "Time"]] = log_resp_df["Timestamp"].apply(
    lambda x: pd.Series([x.date(), x.time()])
)

# Sort the values based on Date and Email
sorted_log_resp_df = log_resp_df.sort_values(by=["Date", "Email Address"])

# Set Date and Email as index, pivots the datetime in two columns based on Action column
final_log_df = sorted_log_resp_df.pivot_table(
    index=["Date", "Email Address"],
    columns="Select your action",
    values="Timestamp",
    aggfunc="first",
)
final_log_df.reset_index(inplace=True)

# Typecast to datetime
final_log_df["Login"] = pd.to_datetime(final_log_df["Login"])
final_log_df["Logout"] = pd.to_datetime(final_log_df["Logout"])

final_log_df["TimeSpent"] = final_log_df["Logout"] - final_log_df["Login"]
logger.info("Response df fetched successfully")

"""**Open Master Sheet**"""
# Open google sheet
master_sheet = gc.open(master_sheet_name)

# Get worksheet
master_worksheet = master_sheet.worksheet(master_worksheet_name)

data = master_worksheet.get_all_values()
master_df = pd.DataFrame(data[1:], columns=data[0])

# Fetches only active candidates from master sheet
cols = ["Admit ID", "Name ", "Email", "Blr LabStatus"]
master_df = master_df[cols]
master_df = master_df.rename(columns={"Name ": "Name", "Email": "Email Address"})
master_df = master_df[master_df["Blr LabStatus"] == "Active"]
logger.info("Master sheet data fetched successfully")

"""**Merge master and response**"""

merged_df = pd.merge(master_df, final_log_df, on="Email Address", how="inner")
# Populate Date from Datetime
merged_df["Date"] = pd.to_datetime(merged_df["Date"]).apply(lambda x: x.date())

# Convert timestamp to hours
to_hrs = lambda x: round(x.total_seconds() / 3600, 2) if pd.notnull(x) else pd.NaT
merged_df["TimeSpent"] = merged_df["TimeSpent"].apply(to_hrs)

# Sort values based on log response date
merged_df.sort_values(by=["Date"], inplace=True)
logger.info("Merged response and master sheet data successfully")

log_sheet = gc.open(log_sheet_name)
# Get or create Dataframe in log csv file
cols = ["Logged At", "Log Date", "Status"]
try:
    log_worksheet = log_sheet.worksheet(log_worksheet_name)
    date_log_df = gspread_dataframe.get_as_dataframe(log_worksheet, usecols=cols)
    date_log_df.dropna(inplace=True, axis=0, how="all")
    logger.info("Last logged date details fetched")
except gspread.exceptions.WorksheetNotFound:
    logger.info("LogDate sheet not found creating new one")
    log_worksheet = log_sheet.add_worksheet(title=log_worksheet_name, rows=50, cols=20)
    date_log_df = pd.DataFrame(columns=cols)
except pd.errors.EmptyDataError:
    logger.info("LogDate sheet found and creating new df")
    date_log_df = pd.DataFrame()
except ValueError:
    logger.info("LogDate sheet found and creating new df")
    date_log_df = pd.DataFrame(columns=cols)

# Fetch unique dates from df
days = merged_df["Date"].unique()

# Fetch existing logged dates from csv file
if not date_log_df.empty:
    existing_dates = pd.to_datetime(date_log_df["Log Date"].unique()).date
else:
    existing_dates = np.array([])

# Get dates to be added to master sheet
new_dates = np.setdiff1d(days, existing_dates)

if not FOR_CURRENT_MONTH:
    today = pd.Timestamp.today()
    start_of_current_month = today.replace(day=1).date()
    start_of_previous_month = (
        (start_of_current_month - pd.DateOffset(months=1)).replace(day=1).date()
    )
    new_dates = [date for date in new_dates if date >= start_of_previous_month]
    logger.info("Fetched data for previous month")


try:
    attendance_sheet = log_sheet.worksheet(title=log_attendance_worksheet_name)
    attendance_df = gspread_dataframe.get_as_dataframe(attendance_sheet)
    attendance_df = attendance_df.loc[:, ~attendance_df.columns.str.contains("^Unnamed")]
    attendance_df.columns = attendance_df.columns.str.replace(r"\.\d+$", "", regex=True)
    attendance_df.columns = pd.MultiIndex.from_arrays(
        [attendance_df.columns, attendance_df.iloc[0].fillna("")]
    )
    attendance_df.drop(0, inplace=True)
    logger.info("Attendance sheet fecthed")

except gspread.exceptions.WorksheetNotFound:
    logger.info("Attendance sheet not found, creating new one")
    today = datetime.today()
    year = today.year
    month = today.month

    no_of_days = calendar.monthrange(year, month)[1]
    attendance_sheet = log_sheet.add_worksheet(
        title=log_attendance_worksheet_name, rows=100, cols=100
    )

    attendance_df = master_df.copy()
    attendance_df.columns = pd.MultiIndex.from_tuples(
        zip(attendance_df.columns, [""] * len(attendance_df.columns))
    )

    gspread_dataframe.set_with_dataframe(attendance_sheet, attendance_df)

attendance_df.dropna(inplace=True, axis=0, how="all")


def determine_status_remarks(row):
    login_is_valid = pd.notna(row["Login"]) and row["Login"] != ""
    logout_is_valid = pd.notna(row["Logout"]) and row["Logout"] != ""

    if login_is_valid and logout_is_valid:
        status = "P"
        remarks = "All good"
    elif login_is_valid and not logout_is_valid:
        status = "P"
        remarks = "Logout not done"
    elif not login_is_valid and logout_is_valid:
        status = "P"
        remarks = "Login not done"
    else:
        status = "A"
        remarks = "Absent"

    return pd.Series([status, remarks])


status_df = merged_df.apply(determine_status_remarks, axis=1)
status_df.columns = ["Status", "Remarks"]
status_df = pd.concat([merged_df, status_df], axis=1)
logger.info("Added new date columns to attendance sheet with remarks")

pivot_df = status_df.pivot(index="Admit ID", columns="Date", values=["Status", "Remarks"])
pivot_df = pivot_df.swaplevel(0, 1, axis=1).sort_index(axis=1, level=0)
pivot_df = pivot_df[
    [col for date in pivot_df.columns.levels[0] for col in [(date, "Status"), (date, "Remarks")]]
]

pivot_df = pivot_df[new_dates]

attendance_df = attendance_df.merge(pivot_df, left_on="Admit ID", right_index=True, how="left")

for date in new_dates:
    attendance_df[(date, "Status")] = attendance_df[(date, "Status")].fillna("A")
    attendance_df[(date, "Remarks")] = attendance_df[(date, "Remarks")].fillna("Absent")

gspread_dataframe.set_with_dataframe(attendance_sheet, attendance_df)
logger.info("Successfully updated attendance worksheet")

# Add new dates to master sheet
dates_lst = []
for date in new_dates:
    time.sleep(1)
    log_dict = {
        "Logged At": pd.Timestamp.now(tz="Asia/Kolkata"),
        "Log Date": date,
        "Status": "Success",
    }

    formated_date = date.strftime("%d-%b-%Y")  # 01-Jan-2024

    # Get or create worksheet
    logger.info("Creating new sheet- %s", formated_date)
    try:
        ws = log_sheet.worksheet(title=str(formated_date))
    except gspread.exceptions.WorksheetNotFound:
        ws = log_sheet.add_worksheet(title=str(formated_date), rows=100, cols=10)

    # Clear and populate new data to worksheet
    try:
        ws.clear()
        df = merged_df[merged_df["Date"] == date]
        gspread_dataframe.set_with_dataframe(ws, df)
    except gspread.exceptions.APIError:
        log_dict["Status"] = "Failed"

    dates_lst.append(log_dict)

# Log the latest dates added to csv file
date_log_df = pd.concat([date_log_df, pd.DataFrame(dates_lst)])
gspread_dataframe.set_with_dataframe(log_worksheet, date_log_df)
logger.info("Last log details updated successfully")
