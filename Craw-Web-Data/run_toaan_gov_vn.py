import json
from scrapers import toaan_gov_vn

source = {
    "id": "toaan_gov_vn",
    "name": "Toà án nhân dân tối cao - Công bố bản án",
    "url": "https://congbobanan.toaan.gov.vn/",
    "output_dir": "D:/Data/toaan_gov_vn",
    "progress_file": "progress_toaan_gov_vn.json",
    "start_id": 1,
    "end_id": 2100000,
    "workers": 10,
    "batch_size": 50
}

global_settings = {}

if __name__ == "__main__":
    toaan_gov_vn.process(source, global_settings)
