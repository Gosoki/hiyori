"""Conditional GET (ETag / Last-Modified) for the feeds we poll often.

The NERV alert feed (every ALERT_REFRESH) and JMA's eqvol.xml (every minute while
P2P is down) almost never change between two polls. Asking "changed since last
time?" costs a few hundred bytes instead of the whole document — 500 KB in JMA's
case — and is what both publishers ask polling clients to do.
"""


async def get_if_changed(client, url, state):
    """GET `url`, or return None when the server answers 304 Not Modified.

    `state` is a per-URL dict the caller keeps between polls; the validators the
    server handed back are stored in it and sent on the next call. A server that
    ignores validators simply keeps answering 200 — nothing here depends on them.
    """
    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    r = await client.get(url, headers=headers)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    if r.headers.get("etag"):
        state["etag"] = r.headers["etag"]
    if r.headers.get("last-modified"):
        state["last_modified"] = r.headers["last-modified"]
    return r
