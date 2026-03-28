import asyncio
from zoneinfo import ZoneInfo
import httpx

from oam.connectors.nl_afm_offering_securities_parsers import parse_detail_page, parse_list_page

REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/openbare-biedingen"
BASE_URL = "https://www.afm.nl"
UA = "oam-bronze-probe/0.1 (+https://github.com/jorgekindelan/oam-bronze)"


async def main():
    tz = ZoneInfo("Europe/Amsterdam")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": UA}) as client:
        r = await client.get(REGISTER_URL)
        r.raise_for_status()

        hits, next_page = parse_list_page(r.text, base_url=BASE_URL, source_tz=tz)
        print("hits_first_page:", len(hits))
        print("next_page:", next_page)
        for h in hits[:5]:
            print(" ", h.record_id, h.published_at_raw, h.offeree_company_raw, h.offeror_raw, "->", h.detail_url)

        if not hits:
            return

        d = await client.get(hits[0].detail_url)
        d.raise_for_status()

        parsed = parse_detail_page(d.text, detail_url=hits[0].detail_url, base_url=BASE_URL, source_tz=tz)
        print("\nDETAIL:")
        print("record_id:", parsed.record_id)
        print("date:", parsed.published_at_raw)
        print("offeree:", parsed.offeree_company_raw)
        print("offeror:", parsed.offeror_raw)
        print("title:", parsed.title_raw)
        print("downloads:", [(x.filename, x.href[:80] + "...") for x in parsed.downloads])

        if parsed.downloads:
            f = await client.get(parsed.downloads[0].href, headers={"Referer": hits[0].detail_url})
            print("\ndownload_status:", f.status_code, "content_type:", f.headers.get("Content-Type"), "bytes:", len(f.content))


if __name__ == "__main__":
    asyncio.run(main())