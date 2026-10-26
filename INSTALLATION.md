# Requirements

- A DIRIGERA hub, its local IP address, and access token (note that the DIRIGERA must be connected to your home network).
- Python 3.11 or newer.
- Node.js 22.12 or newer, including npm.

## Download the Project

1. On this project's GitHub page, click the green **Code** button and choose **Download ZIP**.
2. Extract the downloaded ZIP (double-click it on Mac, or right-click and choose **Extract All** on Windows).
3. Open the extracted project folder. It should contain the `backend` and `frontend` folders and this guide.


## Mac

Open Terminal in the project folder and run:

```zsh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
SMART_HOME_PROVIDER=mock backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open a second terminal in the project folder:

```zsh
cd frontend
npm ci
npm run dev
```

## Windows

Open PowerShell in the project folder and run:

```powershell
python -m venv backend/.venv
./backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
$env:SMART_HOME_PROVIDER = 'mock'
./backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open a second PowerShell window in the project folder:

```powershell
cd frontend
npm ci
npm run dev
```

## Open the app

Visit **http://127.0.0.1:5173** and keep both terminals running. These commands initially use simulated lights.

On later launches, skip the environment creation and dependency installation. Run the launch command and `npm run dev` again. Press **Ctrl+C** in each terminal to stop.

## Connect your lights

Create a private `.env` file in the [backend folder](backend/) with your own values:

```dotenv
DIRIGERA_IP=YOUR_HUB_IP
DIRIGERA_TOKEN=YOUR_HUB_TOKEN
```

Keep this file private. It is ignored by Git. For obtaining a token, see the [dirigera setup guide](https://github.com/Leggin/dirigera).

Stop the first terminal with **Ctrl+C**, then restart from the project folder in real mode instead of mock mode:

**Mac:**

```zsh
SMART_HOME_PROVIDER=dirigera backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

**Windows PowerShell:**

```powershell
$env:SMART_HOME_PROVIDER = 'dirigera'
./backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Leave the second terminal running and refresh the dashboard to see your real lights. To return to simulated lights, restart using the original mock command.
