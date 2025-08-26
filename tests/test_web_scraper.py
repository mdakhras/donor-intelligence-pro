import os
import json
import re
from utils.web_scraper import search_donor_articles, RAW_INPUTS_DIR

def test_scraper_returns_results():
    donor = "Test Org"
    region = "Africa"
    theme = "Climate"

    results = search_donor_articles(donor, region, theme, max_results=2, save_to_disk=True)

    assert isinstance(results, list)
    assert len(results) > 0
    assert "title" in results[0]
    assert "url" in results[0]
    assert "body" in results[0]

def test_output_file_created():
    donor = "Test Org"
    region = "Africa"
    theme = "Climate"

    results = search_donor_articles(donor, region, theme, max_results=1, save_to_disk=True)

    # Check for the most recent file matching naming convention
    slug_prefix = f"{donor.lower().replace(' ', '_')}_{region.lower()}_{theme.lower()}"
    matching_files = [f for f in os.listdir(RAW_INPUTS_DIR) if slug_prefix in f]

    assert len(matching_files) > 0

    # Validate JSON content
    filepath = os.path.join(RAW_INPUTS_DIR, matching_files[-1])
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert isinstance(data, list)
        assert "title" in data[0]
        assert "body" in data[0]
