import os
import requests
import zipfile
import io
import shutil

API_KEY = "c94331fb-7853-415c-b819-7bd131ca47f4"
OPERATORS = [
    "CE", "SS", "PE", "SR", "SA", "CT", "MA", "CM", "BA", "CC", "SC", "SM", "AC", "EE", "GP", "SF"
]
BASE_URL = "http://api.511.org/transit/datafeeds"
BASE_PATH = r"C:\Users\bilal\Desktop\Raptor\Raptor"

def download_and_extract(operator_id):
    url = f"{BASE_URL}?api_key={API_KEY}&operator_id={operator_id}"
    print(f"Downloading data for {operator_id}...")
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to download {operator_id}: {response.status_code}")
            return

        # Create directory
        dir_name = f"GTFSTransitData_{operator_id}"
        full_path = os.path.join(BASE_PATH, dir_name)
        
        # Remove if exists (re-download behavior)
        if os.path.exists(full_path):
            print(f"Removing existing directory {full_path}")
            shutil.rmtree(full_path)
        os.makedirs(full_path)

        # 511.org might return a zip or just XML if error, check content start?
        # Assuming zip for now as per docs.
        try:
            z = zipfile.ZipFile(io.BytesIO(response.content))
            z.extractall(full_path)
            print(f"Extracted to {dir_name}")
        except zipfile.BadZipFile:
            print(f"Bad zip file for {operator_id}. content-type: {response.headers.get('content-type')}")
            # Sometimes 511 returns XML error
            print(response.content[:200])
    except Exception as e:
        print(f"Error processing {operator_id}: {e}")

if __name__ == "__main__":
    for op in OPERATORS:
        download_and_extract(op)
