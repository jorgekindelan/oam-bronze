import asyncio
from zoneinfo import ZoneInfo
import httpx

from oam.connectors.nl_afm_prospectus_approved_parsers import parse_export_csv, parse_detail_downloads

EXPORT = "https://www.afm.nl/export.aspx?format=csv&type=1032d7bb-0c63-4fcf-b64d-82e0a9d74070"
DETAIL_TPL = "https://www.afm.nl/en/sector/registers/meldingenregisters/goedgekeurde-prospectussen/details?id={record_id}"
UA = "oam-bronze-probe/0.1 (+https://github.com/jorgekindelan/oam-bronze)"

async def main():
    tz = ZoneInfo("Europe/Amsterdam")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": UA}) as client:
        r = await client.get(EXPORT)
        r.raise_for_status()
        rows = parse_export_csv(r.content, tz=tz)
        print("rows_parsed:", len(rows))
        for x in rows[:5]:
            print(" ", x.record_id, x.date_approval_raw, x.issuer_name_raw, x.description_raw, x.filename)

        if not rows:
            return

        detail_url = DETAIL_TPL.format(record_id=rows[0].record_id)
        d = await client.get(detail_url)
        d.raise_for_status()
        dls = parse_detail_downloads(d.text, base_url="https://www.afm.nl")
        print("detail_downloads:", [(x.filename, x.href[:80] + "...") for x in dls[:3]])

        if not dls:
            return

        # try download with referer
        f = await client.get(dls[0].href, headers={"Referer": detail_url})
        print("download_status:", f.status_code, "content_type:", f.headers.get("Content-Type"), "bytes:", len(f.content))

if __name__ == "__main__":
    asyncio.run(main())