"""vm106 hosting entrypoint for svarkor-ai/bibliotek (MC#2317).

The vm106 renderer runs `python server.py` with NO PORT env; nginx proxies
sibbamala.com/bibliotek/ -> 127.0.0.1:8140. src/app.py only DEFINES the ASGI `app`;
this shim binds the manifest PORT (default 8140) on 0.0.0.0.
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8140"))
    uvicorn.run("src.app:app", host="0.0.0.0", port=port)
