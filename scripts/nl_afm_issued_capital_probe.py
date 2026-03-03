import asyncio
from zoneinfo import ZoneInfo
import httpx

from oam.connectors.nl_afm_issued_capital_parsers import parse_list_page, parse_detail_page

REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/geplaatst-kapitaal"
BASE_URL = "https://www.afm.nl"

async def main():
    tz = ZoneInfo("Europe/Amsterdam")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(REGISTER_URL, headers={"User-Agent": "oam-bronze-probe/0.1"})
        r.raise_for_status()

        rows, pages = parse_list_page(r.text, base_url=BASE_URL, tz=tz)
        print("rows_on_first_page=", len(rows))
        print("example_rows:")
        for x in rows[:10]:
            print(" ", x.record_id, x.date_raw, x.issuer_name_raw, x.detail_url)

        if not rows:
            raise SystemExit("No rows parsed from register page.")

        # Download one detail HTML and extract ISINs/KvK
        d = await client.get(rows[0].detail_url, headers={"User-Agent": "oam-bronze-probe/0.1"})
        d.raise_for_status()
        parsed = parse_detail_page(d.text)
        print("\nDETAIL PARSED:")
        print(parsed)

if __name__ == "__main__":
    asyncio.run(main())