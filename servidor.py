"""Panel de control HTTP básico para el servidor central de RedLab Tester."""

from fastapi import FastAPI
import uvicorn

app = FastAPI(
    title="RedLab Tester",
    description="Panel de control del servidor central de pruebas EMI.",
    version="0.1.0",
)


@app.get("/status")
async def status() -> dict[str, str]:
    """Indica que el servidor central está listo para recibir pruebas."""
    return {
        "status": "ready",
        "message": "Servidor central listo para recibir pruebas de los clientes.",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
