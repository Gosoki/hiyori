"""Fetch a single exchange rate from open.er-api.com (free, no API key)."""
import httpx

ER_API = "https://open.er-api.com/v6/latest/{base}"


async def fetch_fx(base, quote):
    """Return {base, quote, rate, updated} where 1 base = rate quote."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(ER_API.format(base=base))
        r.raise_for_status()
        data = r.json()
    rate = (data.get("rates") or {}).get(quote)
    if not rate:
        raise ValueError(f"no rate for {base}->{quote}")
    return {"base": base, "quote": quote, "rate": rate,
            "updated": (data.get("time_last_update_utc") or "")[:16]}
