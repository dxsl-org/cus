from datetime import datetime

from pipeline.ingest.open_dataset_export import crawl_time_of


def test_crawl_time_formats_of_supported_datasets():
    assert crawl_time_of("2013-12-09T03:22:04Z") == datetime(2013, 12, 9, 3, 22, 4)      # FineWeb-2
    assert crawl_time_of("2018/09/23 04:49:28") == datetime(2018, 9, 23, 4, 49, 28)      # CulturaX
    assert crawl_time_of(datetime(2021, 8, 3, 22, 56, 50)) == datetime(2021, 8, 3, 22, 56, 50)  # HPLT
