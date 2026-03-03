import asyncio
from zoneinfo import ZoneInfo
import httpx

from oam.connectors.nl_afm_inside_info_parsers import parse_list_page, parse_detail_html, parse_export_csv

REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap"
EXPORT_CSV_URL = "https://www.afm.nl/export.aspx?format=csv&type=fb94a1d1-ee14-4103-b6ca-02ad6ec9d8b6"
BASE_URL = "https://www.afm.nl"

UA = "oam-bronze-probe/0.1 (+https://github.com/jorgekindelan/oam-bronze)"


async def main():
    tz = ZoneInfo("Europe/Amsterdam")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": UA}) as client:
        print("GET register:", REGISTER_URL)
        r = await client.get(REGISTER_URL)
        r.raise_for_status()
        hits, next_page = parse_list_page(r.text, base_url=BASE_URL, source_tz=tz)
        print("hits_first_page:", len(hits))
        for h in hits[:5]:
            print(" ", h.record_id, h.published_at_raw, h.issuer_name_raw, "->", h.detail_url)

        if not hits:
            return

        print("\nGET detail:", hits[0].detail_url)
        d = await client.get(hits[0].detail_url)
        d.raise_for_status()
        parsed = parse_detail_html(d.text, base_url=BASE_URL)
        print("detail issuer:", parsed.get("issuer_name_raw"))
        print("detail title:", parsed.get("title_raw"))
        print("downloads:", [(x.filename, x.href[:60] + "...") for x in parsed.get("related_downloads", [])])

        print("\nGET export CSV:", EXPORT_CSV_URL)
        c = await client.get(EXPORT_CSV_URL)
        c.raise_for_status()
        rows = parse_export_csv(c.text)
        print("csv_rows_parsed:", len(rows))
        for row in rows[:3]:
            print(" ", row)


if __name__ == "__main__":
    asyncio.run(main())