# Cliente web de potencia eólica

## Demo local en Windows

Desde la raíz del proyecto, instala las dependencias del cliente una vez y arranca el demo:

```powershell
cd frontend
npm install
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\scripts\start-demo.ps1
```

Abre la URL `http://127.0.0.1:5173/` (el script elige otros puertos si están ocupados e informa las URLs y el comando de cierre). La API demo escucha solo en `127.0.0.1`. El script crea una copia temporal de la API, el esquema y el modelo inicial, además de un CSV sintético de 100 filas válido para la carga. Su SQLite, el CSV y el modelo reentrenado quedan bajo `%TEMP%`, separados de `database/` y `ciencia-datos/` del proyecto. Los logs también quedan en esa carpeta.

El demo requiere `API_DEMO_MODE=1` y entra por `backend.demo_local`; ese módulo solo acepta solicitudes de loopback y arranca Uvicorn enlazado a `127.0.0.1`. No usa credenciales. `backend.main` conserva HTTP Basic fuera del demo.

## Modo autenticado

Para usar una API normal protegida, inicia el backend como siempre con HTTP Basic. En la terminal que ejecuta Vite define las variables del proxy antes de arrancarlo:

```powershell
$env:API_USERNAME = "usuario-local"
$env:API_PASSWORD = "contraseña-local"
cd frontend
npm run dev
```

Vite adjunta Basic desde su proceso Node al reenviar `/api` a `http://127.0.0.1:8000`. Las credenciales no se envían al navegador ni se guardan en el repositorio; el frontend no solicita acceso.

## Operaciones disponibles

1. **Predicción:** ingresa velocidad en m/s y dirección en grados. Se valida velocidad `>= 0` y dirección `0 <= grados < 360`; la respuesta en kW se registra en el historial.
2. **Historial:** muestra fecha, entradas, potencia y versión. Usa `X-Next-Before-Id` y el parámetro `before_id` para avanzar con un cursor estable; el tamaño de página es 8.
3. **Métricas:** muestra modelo, versión, MAE, RMSE y R² devueltos por `GET /metrics`.
4. **Carga CSV:** elige un `.csv`; el cliente lo envía como `multipart/form-data` en el campo `archivo` y muestra el motivo devuelto si la API lo rechaza.
5. **Reentrenamiento:** tras una carga aceptada, pulsa **Reentrenar**. Se envía el UUID `id_carga`; al terminar se actualizan métricas e historial. Ejecuta una nueva predicción para registrar la versión publicada.
