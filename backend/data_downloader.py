import requests
import zipfile
import io
import os
from concurrent.futures import ThreadPoolExecutor

API_KEY = "c94331fb-7853-415c-b819-7bd131ca47f4"
OPERATORS = [
    "CE", "SS", "PE", "SR", "SA", "CT", "MA", "CM", "BA", "CC", "SC", "SM", "AC", "EE", "GP", "SF"
]
BASE_URL = "http://api.511.org/transit/datafeeds"
DATA_DIR = r"C:\Users\bilal\Desktop\Raptor\Raptor\gtfs_data"

def download_operator_gtfs(operator_id):
    print(f"Downloading GTFS for {operator_id}...")
    params = {
        "api_key": API_KEY,
        "operator_id": operator_id
    }
    
    try:
        response = requests.get(BASE_URL, params=params, stream=True)
        response.raise_for_status()
        
        # 511.org often returns BOM in the zip file or other issues, 
        # but let's assume it's a standard zip.
        z = zipfile.ZipFile(io.BytesIO(response.content))
        
        extract_path = os.path.join(DATA_DIR, operator_id)
        os.makedirs(extract_path, exist_ok=True)
        z.extractall(extract_path)
        print(f"Successfully downloaded and extracted {operator_id}")
    except Exception as e:
        print(f"Error downloading {operator_id}: {e}")

def main():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    with ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(download_operator_gtfs, OPERATORS)

if __name__ == "__main__":
    main()
