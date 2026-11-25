import ipaddress
import os

import uvicorn
from fastapi import HTTPException, Request

from backend.main import app, usuario_autenticado


if os.getenv("API_DEMO_MODE") != "1":
    raise RuntimeError("Activa API_DEMO_MODE=1 solo para ejecutar el demo local.")


def usuario_demo_local(request: Request):
    direccion = request.client.host if request.client else ""
    try:
        es_loopback = ipaddress.ip_address(direccion).is_loopback
    except ValueError:
        es_loopback = False
    if not es_loopback:
        raise HTTPException(status_code=403, detail="El demo solo admite conexiones locales.")
    return "demo-local"


app.dependency_overrides[usuario_autenticado] = usuario_demo_local


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("API_PORT", "8001")))